// ── codemod: migrate data-tip / title → <Tooltip> wrapper ──
// One-shot AST transform. Scans src/**/*.{tsx,ts}, wraps interactive elements
// bearing data-tip or title attributes with <Tooltip tip={...}>...</Tooltip>.
// Run: node scripts/migrate-data-tip.mjs

import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execSync } from 'node:child_process'

import { parse } from '@babel/parser'
import _traverse from '@babel/traverse'
import generate from '@babel/generator'

// CJS default export interop for ESM import
const traverse = _traverse.default || _traverse
const gen = generate.default || generate

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const SRC_DIR = resolve(__dirname, '..', 'src')

const TOOLTIP_IMPORT_PATH = '@/components/ui/Tooltip'

// Interactive elements whose `title=` is semantically a tooltip (not form help).
const INTERACTIVE_TAGS = new Set([
  'button', 'a', 'span', 'i', 'svg', 'img', 'div',
])

// Collect .tsx/.ts files under src/.
function collectFiles() {
  let files = []
  try {
    const out = execSync('git ls-files "src/**/*.tsx" "src/**/*.ts"', {
      cwd: dirname(SRC_DIR),
      encoding: 'utf8',
    })
    files = out.split('\n').filter(Boolean).map(f => resolve(dirname(SRC_DIR), f))
  } catch {
    function walk(d) {
      for (const entry of readdirSync(d)) {
        const full = join(d, entry)
        const st = statSync(full)
        if (st.isDirectory()) walk(full)
        else if (/\.(tsx|ts)$/.test(entry)) files.push(full)
      }
    }
    walk(SRC_DIR)
  }
  return files
}

// Check if a JSXOpeningElement is an interactive tag.
function isInteractiveTag(path) {
  const nameNode = path.node.name
  if (nameNode.type !== 'JSXIdentifier') return false
  return INTERACTIVE_TAGS.has(nameNode.name.toLowerCase())
}

// Check if element has role="button" (for div).
function hasRoleButton(path) {
  for (const attr of path.node.attributes) {
    if (attr.type === 'JSXAttribute' && attr.name.name === 'role') {
      if (attr.value?.type === 'StringLiteral' && attr.value.value === 'button') {
        return true
      }
    }
  }
  return false
}

// Find a named attribute on a JSXOpeningElement. Returns {attr, index} or null.
function findAttr(path, name) {
  for (let i = 0; i < path.node.attributes.length; i++) {
    const attr = path.node.attributes[i]
    if (attr.type === 'JSXAttribute' && attr.name.name === name) {
      return { attr, index: i }
    }
  }
  return null
}

// Check if ancestor chain already contains a <Tooltip> wrapper (avoid nesting).
function isAlreadyInsideTooltip(path) {
  let parent = path.parentPath
  while (parent) {
    if (parent.isJSXElement()) {
      const name = parent.node.openingElement.name
      if (name.type === 'JSXIdentifier' && name.name === 'Tooltip') return true
    }
    parent = parent.parentPath
  }
  return false
}

// Ensure file has `import { Tooltip } from '@/components/ui/Tooltip'`.
function ensureTooltipImport(ast) {
  let hasImport = false
  for (const node of ast.program.body) {
    if (node.type !== 'ImportDeclaration') continue
    if (node.source.value === TOOLTIP_IMPORT_PATH) {
      for (const spec of node.specifiers) {
        if (spec.type === 'ImportSpecifier' && spec.imported.name === 'Tooltip') {
          hasImport = true
        }
      }
    }
  }
  if (hasImport) return false

  // Build import declaration node.
  const importDecl = {
    type: 'ImportDeclaration',
    source: { type: 'StringLiteral', value: TOOLTIP_IMPORT_PATH },
    specifiers: [{
      type: 'ImportSpecifier',
      imported: { type: 'Identifier', name: 'Tooltip' },
      local: { type: 'Identifier', name: 'Tooltip' },
    }],
  }
  // Insert at top of program body (before first statement).
  ast.program.body.unshift(importDecl)
  return true
}

// Build a JSX element node: <Tooltip tip={expr} side="...">...children...</Tooltip>
function buildTooltipWrapper(tipExpr, sideValue, originalElement) {
  // JSXAttribute.value must be StringLiteral or JSXExpressionContainer.
  // If tipExpr is a bare expression (Identifier / Call / Member / etc.),
  // wrap it. If it's already a StringLiteral, use directly.
  const tipValue = tipExpr.type === 'StringLiteral'
    ? tipExpr
    : { type: 'JSXExpressionContainer', expression: tipExpr }

  const attributes = [
    {
      type: 'JSXAttribute',
      name: { type: 'JSXIdentifier', name: 'tip' },
      value: tipValue,
    },
  ]
  if (sideValue) {
    attributes.push({
      type: 'JSXAttribute',
      name: { type: 'JSXIdentifier', name: 'side' },
      value: { type: 'StringLiteral', value: sideValue },
    })
  }
  return {
    type: 'JSXElement',
    openingElement: {
      type: 'JSXOpeningElement',
      name: { type: 'JSXIdentifier', name: 'Tooltip' },
      attributes,
      selfClosing: false,
    },
    closingElement: {
      type: 'JSXClosingElement',
      name: { type: 'JSXIdentifier', name: 'Tooltip' },
    },
    children: [originalElement],
  }
}

// Convert a JSXAttribute value node to the value to pass as `tip=`.
// data-tip="str" → StringLiteral "str"
// data-tip={expr} → JSXExpressionContainer(expr) (pass expr directly)
function attrValueToTipExpr(attrValue) {
  if (!attrValue) return { type: 'StringLiteral', value: '' }
  if (attrValue.type === 'StringLiteral') {
    return { type: 'StringLiteral', value: attrValue.value }
  }
  if (attrValue.type === 'JSXExpressionContainer') {
    return attrValue.expression
  }
  // JSXFragment / other rare — wrap as expression.
  return attrValue
}

function processFile(filePath) {
  const code = readFileSync(filePath, 'utf8')
  let ast
  try {
    ast = parse(code, {
      sourceType: 'module',
      plugins: ['typescript', 'jsx'],
      errorRecovery: true,
    })
  } catch (e) {
    console.error(`  ! parse failed: ${relative(SRC_DIR, filePath)} — ${e.message}`)
    return 0
  }

  let changed = 0
  let addedImport = false

  // Collect replacement targets first (can't mutate during traverse reliably).
  const replacements = []

  traverse(ast, {
    JSXOpeningElement(path) {
      if (isAlreadyInsideTooltip(path)) return
      if (!isInteractiveTag(path)) {
        // Non-interactive tag: only migrate if it has data-tip (not title).
        // title on non-interactive elements (e.g. input) is form semantics, skip.
      }

      const dataTipAttr = findAttr(path, 'data-tip')
      let targetAttr = null
      let attrName = null

      if (dataTipAttr) {
        targetAttr = dataTipAttr
        attrName = 'data-tip'
      } else if (isInteractiveTag(path) || hasRoleButton(path)) {
        const titleAttr = findAttr(path, 'title')
        if (titleAttr) {
          targetAttr = titleAttr
          attrName = 'title'
        }
      }

      if (!targetAttr) return

      const placementAttr = findAttr(path, 'data-tip-placement')
      let sideValue = null
      if (placementAttr) {
        const v = placementAttr.attr.value
        if (v?.type === 'StringLiteral' && (v.value === 'right' || v.value === 'left')) {
          sideValue = v.value
        }
      }

      const tipExpr = attrValueToTipExpr(targetAttr.attr.value)

      replacements.push({
        openingPath: path,
        attrName,
        targetAttrIndex: targetAttr.index,
        placementAttrIndex: placementAttr ? placementAttr.index : null,
        tipExpr,
        sideValue,
      })
    },
  })

  if (replacements.length === 0) return 0

  // Apply replacements: for each, remove the attributes from opening element,
  // then replace the JSXElement (parent) with the Tooltip wrapper.
  // Process in reverse order to keep path references stable-ish; but since
  // we re-resolve via the stored openingPath.parentPath, order matters less.
  for (const r of replacements) {
    const opening = r.openingPath.node
    // Remove data-tip/title and data-tip-placement attributes.
    const toRemove = new Set([r.targetAttrIndex, r.placementAttrIndex].filter(i => i !== null))
    opening.attributes = opening.attributes.filter((_, i) => !toRemove.has(i))

    const elementPath = r.openingPath.parentPath // JSXElement
    if (!elementPath || !elementPath.isJSXElement()) {
      console.error(`  ! could not resolve JSXElement for ${attrName} in ${relative(SRC_DIR, filePath)}`)
      continue
    }

    const wrapper = buildTooltipWrapper(r.tipExpr, r.sideValue, elementPath.node)
    elementPath.replaceWith(wrapper)
    changed++
  }

  if (changed > 0) {
    addedImport = ensureTooltipImport(ast)
    const output = gen(ast, {
      retainLines: false,
      comments: true,
      jsescOption: { minimal: true },
    }).code + '\n'

    writeFileSync(filePath, output, 'utf8')
  }

  return changed
}

// ── main ──
const files = collectFiles()
console.log(`Scanning ${files.length} files under ${relative(process.cwd(), SRC_DIR)}/...`)

let totalChanged = 0
let filesChanged = 0
for (const f of files) {
  const n = processFile(f)
  if (n > 0) {
    console.log(`  ✓ ${relative(SRC_DIR, f)} — ${n} tip(s) migrated`)
    totalChanged += n
    filesChanged++
  }
}

console.log(`\nDone: ${totalChanged} tip attribute(s) migrated across ${filesChanged} file(s).`)
