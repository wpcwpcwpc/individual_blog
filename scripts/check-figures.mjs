/**
 * 口径一致性校验 —— 构建前置硬闸门。
 *
 * 全站量化事实的唯一定义处是 GLOSSARY.md 第三节「指标区间化登记」；
 * 项目页 frontmatter 的 `figures` 只声明"页面展示哪几项、展示成什么"。
 * 本脚本逐项比对两处取值，不一致即失败 —— 数字打架在构建期就被拦住，
 * 不会等到面试官去数。
 *
 * 登记表为空同样失败（不允许校验空转）。
 */
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const glossaryPath = path.join(root, 'GLOSSARY.md');
const projectsDir = path.join(root, 'src', 'content', 'projects');

/** 解析 GLOSSARY.md 第三节：指标项 → 对外表述 */
function parseRegister(text) {
  const section = text.split('## 三、')[1]?.split('\n## ')[0] ?? '';
  const rows = new Map();

  for (const line of section.split('\n')) {
    const cells = line.split('|').map((cell) => cell.trim());
    // 表格行的形态：[空, 指标项, 对外表述, 来源说明, 空]
    if (cells.length < 5) continue;
    const [, item, expression] = cells;
    if (!item || !expression) continue;
    if (/^-{2,}$/.test(item) || item === '指标项') continue; // 分隔行与表头
    rows.set(item, expression);
  }

  return rows;
}

/** 取出 markdown 的 frontmatter 文本 */
function frontmatterOf(text) {
  const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  return match ? match[1] : '';
}

/** 解析 frontmatter 中的 figures 列表（只认本仓库约定的三项字段） */
function figuresOf(frontmatter) {
  const block = frontmatter.split(/^figures:\s*$/m)[1];
  if (!block) return [];

  const figures = [];
  for (const chunk of block.split(/^\s*-\s+/m).slice(1)) {
    // 列表项缩进结束后即视为本块结束
    const body = chunk.split(/\n\S/)[0];
    const pick = (key) => body.match(new RegExp(`^\\s*${key}:\\s*(.+)$`, 'm'))?.[1]?.trim();
    const register = pick('register');
    const value = pick('value')?.replace(/^["']|["']$/g, '');
    if (!register || !value) continue;
    figures.push({ register, label: pick('label'), value });
  }

  return figures;
}

let glossary;
try {
  glossary = await readFile(glossaryPath, 'utf8');
} catch {
  console.error('[figures] FAIL GLOSSARY.md 不存在 —— 口径登记缺失，构建中止');
  process.exit(1);
}

const register = parseRegister(glossary);
if (register.size === 0) {
  console.error('[figures] FAIL GLOSSARY.md 第三节「指标区间化登记」为空 —— 校验失效，构建中止');
  process.exit(1);
}

const entries = await readdir(projectsDir, { withFileTypes: true }).catch(() => []);
const files = entries
  .filter((entry) => entry.isFile() && entry.name.endsWith('.md'))
  .map((entry) => path.join(projectsDir, entry.name));

const failures = [];
const used = new Set();
let checked = 0;

for (const file of files) {
  const figures = figuresOf(frontmatterOf(await readFile(file, 'utf8')));
  for (const figure of figures) {
    checked += 1;
    used.add(figure.register);
    const registered = register.get(figure.register);
    const where = `${path.relative(root, file)} → ${figure.label ?? figure.register}`;

    if (registered === undefined) {
      failures.push(`  ${where}\n    登记表中没有「${figure.register}」这一项 —— 先登记再展示`);
    } else if (registered !== figure.value) {
      failures.push(
        `  ${where}\n    页面取值「${figure.value}」≠ 登记表述「${registered}」 —— 改一处必须同步另一处`,
      );
    }
  }
}

console.log(
  `[figures] 登记 ${register.size} 项 · 页面引用 ${checked} 处 · 覆盖 ${files.length} 个项目页`,
);

if (failures.length > 0) {
  console.error(`\n[figures] FAIL 口径不一致，构建中止：\n\n${failures.join('\n\n')}\n`);
  console.error('  处理方式：以 GLOSSARY.md 第三节为准，修正项目页 frontmatter 的 figures。');
  process.exit(1);
}

const unused = [...register.keys()].filter((item) => !used.has(item));
if (unused.length > 0) {
  console.log(`[figures] 尚未展示的登记项：${unused.join('、')}`);
}

console.log('[figures] PASS 页面取值与登记一致');
