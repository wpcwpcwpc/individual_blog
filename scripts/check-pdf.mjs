/**
 * PDF 审计 —— 构建前置硬闸门（补齐"文本闸门扫不到 PDF"的盲区）。
 *
 * 简历、附件、导出物都是 PDF，二进制不进 `sanitize-check.mjs` 的扫描范围；
 * 这里把 `public/` 下所有会被发布的 PDF 抽成文本，跑同一套禁出词表，
 * 再叠加 PII 模式（手机号 / 身份证 / 固话）。命中即失败，构建中止。
 *
 * 用法：
 *   node scripts/check-pdf.mjs            # 闸门模式
 *   node scripts/check-pdf.mjs --dump     # 额外打印全文，供人工通读（自动扫不到的语义风险）
 */
import { readdir, readFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const publicDir = path.join(root, 'public');
const dump = process.argv.includes('--dump');

/**
 * 词表存放处 —— 与 sanitize-check.mjs 同源同规则。
 * 公开仓库 MUST NOT 登记内部标识，词表因此不在仓库里：CI 由 secret 注入，本地读 gitignored 文件。
 */
const wordsPath = path.join(root, 'GLOSSARY.local.md');

/** 公开仓库的 CI 日志同样公开：默认打码，本地要看全文设 SANITIZE_REVEAL=1 */
const REVEAL = process.env.SANITIZE_REVEAL === '1';
const maskToken = (token) => (REVEAL ? token : `${token.slice(0, 2)}***（${token.length} 字符）`);

/** PII 模式：不在禁出词表里（词表登记的是内部标识），但公开 PDF MUST NOT 出现 */
const PII_PATTERNS = [
  { pattern: '\\b1[3-9]\\d{9}\\b', name: '手机号' },
  { pattern: '\\b\\d{17}[\\dXx]\\b', name: '身份证号' },
  { pattern: '\\b0\\d{2,3}-\\d{7,8}\\b', name: '固话' },
  { pattern: '\\b\\d{3,4}-\\d{7,8}\\b', name: '固话' },
];

/**
 * 解析词表的禁出词（与 scripts/sanitize-check.mjs 同源同规则；
 * 刻意各留一份，避免为了复用去改动已验证过的闸门实现）。
 */
function parseGlossary(text) {
  const sectionTwo = text.split('## 二、禁出词表')[1]?.split('\n## ')[0] ?? '';

  const exactSection = sectionTwo.split('### 2.1 精确词')[1]?.split('###')[0] ?? '';
  const seen = new Set();
  const exact = [];
  for (const line of (exactSection.match(/```[\s\S]*?```/)?.[0] ?? '').replace(/```/g, '').split('\n')) {
    const word = line.trim();
    const key = word.toLowerCase();
    if (!word || seen.has(key)) continue;
    seen.add(key);
    exact.push(word);
  }

  const regexSection = sectionTwo.split('### 2.2 正则模式')[1] ?? '';
  const patterns = [];
  for (const line of regexSection.split('\n')) {
    const match = line.match(/^\|\s*`(.+?)`\s*\|/);
    if (!match) continue;
    patterns.push(match[1].replace(/\\\|/g, '|'));
  }

  return { exact, patterns };
}

async function collectPdfs(dir) {
  const found = [];
  for (const entry of await readdir(dir, { recursive: true, withFileTypes: true }).catch(() => [])) {
    if (entry.isFile() && entry.name.toLowerCase().endsWith('.pdf')) {
      found.push(path.join(entry.parentPath ?? entry.path, entry.name));
    }
  }
  return found;
}

const inlineWords = process.env.GLOSSARY_WORDS?.trim();
const glossary = inlineWords || (await readFile(wordsPath, 'utf8').catch(() => null));
if (!glossary) {
  console.error(
    '[pdf] FAIL 取不到禁出词表 —— 闸门失效，构建中止\n' +
      `  本地：把词表写到 ${path.relative(root, wordsPath)}（不入库）\n` +
      '  CI：在仓库 secret 里配置 GLOSSARY_WORDS（值为词表全文）',
  );
  process.exit(1);
}

const { exact, patterns } = parseGlossary(glossary);
if (exact.length === 0 || patterns.length === 0) {
  console.error('[pdf] FAIL 禁出词表为空 —— 闸门失效，构建中止');
  process.exit(1);
}

/**
 * 豁免清单 —— 经作者确认不需要脱敏的对外文档。
 * 简历是主动投递的对外材料，雇主名称与个人联系方式本来就公开；
 * 豁免不等于不检查：命中仍然打印告警，让"这份 PDF 里有什么是公开的"始终可见，
 * 只是不拦构建。新增豁免 MUST 写明理由。
 */
const EXEMPT = [
  { file: 'public/resume/resume.pdf', reason: '对外投递的简历：个人身份与雇主名称本就可公开（见 RESUME.md）' },
];

function isExempt(relPath) {
  return EXEMPT.find((item) => item.file === relPath.replace(/\\/g, '/'));
}

const pdfs = await collectPdfs(publicDir);
if (pdfs.length === 0) {
  console.log('[pdf] public/ 下暂无可发布 PDF，跳过');
  process.exit(0);
}

const pdfjs = await import(pathToFileURL(require.resolve('pdfjs-dist/legacy/build/pdf.mjs')).href);
const report = [];
const warnings = [];
let pageCount = 0;

for (const file of pdfs) {
  const doc = await pdfjs.getDocument({ data: new Uint8Array(await readFile(file)) }).promise;
  const hits = [];

  for (let page = 1; page <= doc.numPages; page += 1) {
    pageCount += 1;
    const content = await (await doc.getPage(page)).getTextContent();
    // 逐字拼接：PDF 抽出的文本会把词拆在不同片段里，用空格连接反而更易漏
    const text = content.items.map((item) => item.str).join('');

    if (dump) {
      console.log(`\n----- ${path.relative(root, file)} 第 ${page} 页 -----\n${text}`);
    }

    for (const word of exact) {
      if (text.toLowerCase().includes(word.toLowerCase())) {
        hits.push(`第 ${page} 页  命中禁出词「${maskToken(word)}」`);
      }
    }
    for (const { pattern, name } of [
      ...patterns.map((pattern) => ({ pattern, name: '禁出模式' })),
      ...PII_PATTERNS,
    ]) {
      for (const match of text.matchAll(new RegExp(pattern, 'g'))) {
        hits.push(`第 ${page} 页  命中${name}「${maskToken(match[0])}」`);
      }
    }
  }

  if (hits.length > 0) {
    const rel = path.relative(root, file).replace(/\\/g, '/');
    const exempt = isExempt(rel);
    const body = `  ${rel}${exempt ? `（已豁免：${exempt.reason}）` : ''}\n${hits.map((hit) => `    ${hit}`).join('\n')}`;
    if (exempt) {
      warnings.push(body);
    } else {
      report.push(body);
    }
  }
}

const exemptCount = pdfs.filter((file) => isExempt(path.relative(root, file))).length;
console.log(`[pdf] 审计 ${pdfs.length} 个 PDF · ${pageCount} 页 · 禁出词 ${exact.length} 条 · 模式 ${patterns.length + PII_PATTERNS.length} 条 · 豁免 ${exemptCount} 个 · 词表来源=${inlineWords ? 'CI secret' : '本地文件'}`);

if (warnings.length > 0) {
  console.warn(`\n[pdf] WARN 已豁免文档命中敏感内容（不拦构建，但请确认这些都是可公开信息）：\n\n${warnings.join('\n\n')}\n`);
}

if (report.length > 0) {
  console.error(`\n[pdf] FAIL 待发布 PDF 命中敏感内容，构建中止：\n\n${report.join('\n\n')}\n`);
  console.error('  处理方式：出对外脱敏版（删 PII、按词表第一节泛化），或先不要放进 public/。');
  console.error('  查看命中原文：本地设 SANITIZE_REVEAL=1 重跑。');
  process.exit(1);
}

console.log('[pdf] PASS 待发布 PDF 未命中敏感内容');
