/**
 * 专栏导入 —— 把外部目录里的长文一次性导入为专栏章节（写入 `src/content/posts/`）。
 *
 * 边界（对应 specs/series-import）：
 * - 只收章节目录里登记的号段；号段 25–28 是公司内部工程内容，默认命中即中止；
 *   内部内容与白名单章节常常同目录共存，此时必须显式 `--skip-internal` 才继续（并打印跳过清单）
 * - 源目录只读；源绝对路径不写进仓库，产物 frontmatter 只记文件名与内容摘要
 * - 内网标识在写入前按规则表改写，残留由构建前置脱敏扫描硬拦
 * - 章节互链改写成站内 URL；解不出来的引用降级为纯文本并打印清单，绝不产出死链
 *
 * 用法：
 *   npm run import:series -- --src <文档目录>
 */
import { readdir, readFile, writeFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outDir = path.join(root, 'src', 'content', 'posts');

const SERIES_ID = 'claudecode';
const TAGS = ['Claude Code', 'Agent', '源码解析'];

/**
 * 章节登记表 —— 序号 → slug 短名 + 分区。
 * slug 一经发布不得变更（等同 URL 契约）；未登记的号段直接报错，不允许脚本猜一个。
 * 完整路径为 /claudecode/<NN>-<slug>，文件名带专栏前缀以便在 posts 目录里自解释。
 */
const CHAPTERS = [
  { order: 0, slug: 'reading-guide', group: '导读' },
  { order: 1, slug: 'project-overview', group: '核心架构' },
  { order: 2, slug: 'data-flow-lifecycle', group: '核心架构' },
  { order: 3, slug: 'query-engine', group: '核心架构' },
  { order: 4, slug: 'message-system', group: '核心架构' },
  { order: 5, slug: 'prompt-engineering', group: '核心架构' },
  { order: 6, slug: 'tool-system', group: '工具与能力扩展' },
  { order: 7, slug: 'core-tools', group: '工具与能力扩展' },
  { order: 8, slug: 'mcp-integration', group: '工具与能力扩展' },
  { order: 9, slug: 'agent-system', group: 'Agent 核心系统' },
  { order: 10, slug: 'agent-memory-state', group: 'Agent 核心系统' },
  { order: 11, slug: 'agent-collaboration', group: 'Agent 核心系统' },
  { order: 12, slug: 'builtin-agents', group: 'Agent 核心系统' },
  { order: 13, slug: 'git-integration', group: '安全与可观测性' },
  { order: 15, slug: 'observability', group: '安全与可观测性' },
  { order: 16, slug: 'security-sandbox', group: '安全与可观测性' },
  { order: 17, slug: 'extension-plugins', group: '安全与可观测性' },
  { order: 18, slug: 'multimodal', group: '工程支撑' },
  { order: 19, slug: 'testing-quality', group: '工程支撑' },
  { order: 20, slug: 'build-packaging', group: '工程支撑' },
  { order: 21, slug: 'config-environment', group: '工程支撑' },
  { order: 23, slug: 'code-style', group: '工程支撑' },
  { order: 24, slug: 'recipes-extension', group: '工程支撑' },
];

/** 内部内容号段：出现即中止，不导入、不只告警 */
const INTERNAL_ORDERS = [25, 26, 27, 28];

/** 内网标识改写规则表（源文档不可改，改写只发生在导入这一步） */
const REDACTIONS = [
  {
    pattern: /(?<![\w.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?::\d{2,5})?/g,
    to: '内网地址',
    label: '内网 IP',
  },
  { pattern: /[\w.-]+\.(?:netease|corp)\.com/g, to: '内部域名', label: '内部域名' },
  { pattern: /[A-Za-z]:\\[\w.-]+\\[\w\\.-]+/g, to: '本地路径', label: '本地路径' },
  { pattern: /(?<![\w.])\/(?:home|data|srv)\/[\w/.-]+/g, to: '本地路径', label: '服务端路径' },
  {
    pattern: /svn:\/\/[\w-]+(?:\.[\w-]+)+[\w.:/-]*/g,
    to: '内部版本库地址',
    label: '版本库地址',
  },
  {
    // 只认带域名的地址：`git@host:owner/repo` 这类占位写法不含内部信息
    pattern: /git@(?!github\.com|gitlab\.com|bitbucket\.org)[\w-]+(?:\.[\w-]+)+:[\w/.-]+/g,
    to: '内部代码托管地址',
    label: '托管地址',
  },
  {
    pattern: /(?<![\w@.\-#])\d{6,9}(?![\w@.\-])/g,
    to: '内部编号',
    label: '内部编号',
  },
];

// ── 参数 ────────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
const srcArg = args.includes('--src') ? args[args.indexOf('--src') + 1] : process.env.SERIES_SRC;
const skipInternal = args.includes('--skip-internal');

if (!srcArg) {
  console.error('[import] 缺少源目录：npm run import:series -- --src <dir>');
  console.error('  提示：源目录路径不写进仓库，每次由执行者传入。');
  process.exit(1);
}

const srcDir = path.resolve(srcArg);

// ── 工具 ────────────────────────────────────────────────────────────────

const pad = (order) => String(order).padStart(2, '0');
const byOrder = new Map(CHAPTERS.map((chapter) => [chapter.order, chapter]));
const urlOf = (order) => `/claudecode/${pad(order)}-${byOrder.get(order).slug}`;
const fileOf = (order) => `${SERIES_ID}-${pad(order)}-${byOrder.get(order).slug}.md`;

/** YAML 双引号标量 */
function yamlString(value) {
  return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}

/** 提取 > **标签**：内容 形式的头部说明 */
function quoteValue(text, label) {
  const match = text.match(new RegExp(`^\\s*>\\s*\\*{0,2}${label}\\*{0,2}\\s*[：:]\\s*(.+)$`, 'm'));
  return match ? match[1].trim() : undefined;
}

function titleOf(text, fallbackName) {
  const h1 = text.match(/^#\s+(.+)$/m)?.[1]?.trim();
  if (!h1) return fallbackName;
  // 源文档 H1 有三种写法：`01-标题` / `04 - 标题` / `第 16 篇：标题`
  return h1.replace(/^\d\d\s*[-–—]\s*/, '').replace(/^第\s*\d+\s*篇[：:]\s*/, '');
}

/** 摘要：优先源文档自带的「一句话理解」，否则取正文首个普通段落 */
function summaryOf(text) {
  const stated = quoteValue(text, '一句话理解');
  if (stated) return stated.replace(/[*`]/g, '').trim();

  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#') || trimmed.startsWith('>') || trimmed.startsWith('|')) continue;
    if (trimmed.startsWith('-') || trimmed.startsWith('```') || trimmed.startsWith('┌')) continue;
    const plain = trimmed.replace(/[*`\[\]]/g, '').replace(/\([^)]*\)/g, '').trim();
    if (plain.length >= 12) return plain.slice(0, 110);
  }
  return 'Claude Code CLI 源码拆解章节';
}

/** 内网标识改写 */
function redact(text) {
  const counts = new Map();
  let out = text;
  for (const rule of REDACTIONS) {
    out = out.replace(new RegExp(rule.pattern.source, 'g'), () => {
      counts.set(rule.label, (counts.get(rule.label) ?? 0) + 1);
      return rule.to;
    });
  }
  return { text: out, counts };
}

/** 章节互链改写：`./NN-xxx.md` → `/claudecode/<NN>-<slug>`；解不出的引用降级为纯文本 */
function rewriteLinks(text) {
  const unresolved = [];
  const leftovers = [];
  const lines = text.split('\n');
  let inFence = false;

  const rewritten = lines.map((line) => {
    if (/^\s*```/.test(line)) {
      inFence = !inFence;
      return line;
    }
    if (inFence) return line; // 代码块里出现的是"文件名示例"或命令行，不动


    /* 目标里可能带括号（如 `./08-模型上下文协议(MCP)深度集成.md`），所以不能简单用 [^)]+ */
    return line.replace(
      /\[([^\]]+)\]\(([^()\s]*(?:\([^()]*\)[^()\s]*)*)(\s+"[^"]*")?\)/g,
      (whole, label, target, title = '') => {
        const [filepart, anchor] = target.split('#');
        const order = Number(filepart.match(/(?:^\.?\/?)(\d\d)-[^/]*\.md$/)?.[1] ?? NaN);
        if (!Number.isFinite(order)) return whole;
        if (!byOrder.has(order)) {
          unresolved.push({ label, target });
          return label; // 指向已归档/不在册的文档：只留文字
        }
        return `[${label}](${urlOf(order)}${anchor ? `#${anchor}` : ''}${title})`;
      },
    );
  });

  // 改写完成后再扫一遍残留的相对文档引用：相对链接连链接检查都不管，会静默变线上死链
  const joined = rewritten.join('\n');
  for (const match of joined.matchAll(/\]\(((?:\.\/)?\d\d-[^)\s]*\.md[^)\s]*)\)/g)) {
    leftovers.push(match[1]);
  }

  return { text: joined, unresolved, leftovers };
}

// ── 校验源目录 ──────────────────────────────────────────────────────────

let sourceFiles;
try {
  sourceFiles = (await readdir(srcDir, { withFileTypes: true }))
    .filter((entry) => entry.isFile() && entry.name.endsWith('.md'))
    .map((entry) => entry.name);
} catch (error) {
  console.error(`[import] 读不到源目录：${error.message}`);
  process.exit(1);
}

const numbered = sourceFiles
  .map((name) => ({ name, order: Number(name.match(/^(\d\d)-/)?.[1] ?? NaN) }))
  .filter((item) => Number.isFinite(item.order));

const internal = numbered.filter((item) => INTERNAL_ORDERS.includes(item.order));
if (internal.length > 0) {
  if (!skipInternal) {
    console.error('[import] FAIL 源目录含公司内部内容，导入中止：');
    for (const item of internal) console.error(`  ${item.name}`);
    console.error('  处理方式：内部号段（25–28）MUST NOT 进入公开站点。');
    console.error('  若内部内容与本次要导入的章节同目录共存，显式跳过：npm run import:series -- --src <dir> --skip-internal');
    process.exit(1);
  }
  console.log(`[import] 已按 --skip-internal 跳过内部内容 ${internal.length} 个（不进产物）：`);
  for (const item of internal) console.log(`  ✗ ${item.name}`);
}

const unknown = numbered.filter(
  (item) => !byOrder.has(item.order) && !INTERNAL_ORDERS.includes(item.order),
);
if (unknown.length > 0) {
  console.error('[import] FAIL 源目录含未登记号段（不允许自动生成 slug）：');
  for (const item of unknown) console.error(`  ${item.name}`);
  console.error('  处理方式：先在 scripts/import-series.mjs 的 CHAPTERS 中登记 slug 与分区。');
  process.exit(1);
}

const absent = CHAPTERS.filter((chapter) => !numbered.some((item) => item.order === chapter.order));
if (absent.length > 0) {
  console.error('[import] FAIL 登记表里的章节在源目录缺失：');
  for (const chapter of absent) console.error(`  ${pad(chapter.order)}-${chapter.slug}`);
  process.exit(1);
}

// ── 逐章导入 ────────────────────────────────────────────────────────────

const redactionCounts = new Map();
const unresolvedAll = [];
const leftoversAll = [];
const fenceOdd = [];
let written = 0;

for (const chapter of CHAPTERS) {
  const sourceName = numbered.find((item) => item.order === chapter.order).name;
  const sourcePath = path.join(srcDir, sourceName);

  const raw = await readFile(sourcePath, 'utf8');
  const info = await stat(sourcePath);
  const normalized = raw.replace(/\r\n/g, '\n').replace(/^\uFEFF/, '');

  const title = titleOf(normalized, sourceName.replace(/\.md$/, ''));
  const summary = summaryOf(normalized);
  const reading = Number(quoteValue(normalized, '阅读时长')?.match(/(\d+)/)?.[1] ?? NaN);
  const weight = [...normalized.slice(0, 4000).matchAll(/⭐/g)].length;

  const withoutH1 = normalized.replace(/^#\s+.+\n/, '');
  const { text: redacted, counts } = redact(withoutH1);
  for (const [label, count] of counts) {
    redactionCounts.set(label, (redactionCounts.get(label) ?? 0) + count);
  }

  const { text: body, unresolved, leftovers } = rewriteLinks(redacted);
  for (const item of unresolved) unresolvedAll.push({ chapter: pad(chapter.order), ...item });
  for (const item of leftovers) leftoversAll.push({ chapter: pad(chapter.order), target: item });

  // 围栏配对自检：源文档少一个开 ``` 会让紧随其后的正文被当成代码块渲染
  const fenceLines = body
    .split('\n')
    .map((line, index) => ({ line, index: index + 1 }))
    .filter(({ line }) => /^\s*```/.test(line));
  if (fenceLines.length % 2 !== 0) {
    fenceOdd.push({ chapter: pad(chapter.order), slug: chapter.slug, count: fenceLines.length });
  }

  const frontmatter = [
    '---',
    `title: ${yamlString(title)}`,
    `summary: ${yamlString(summary)}`,
    `publishedAt: ${info.mtime.toISOString().slice(0, 10)}`,
    `tags: [${TAGS.map(yamlString).join(', ')}]`,
    `series: ${SERIES_ID}`,
    `seriesOrder: ${chapter.order}`,
    `seriesGroup: ${chapter.group}`,
    ...(Number.isFinite(reading) ? [`readingMinutes: ${reading}`] : []),
    ...(weight > 0 ? [`weight: ${Math.min(weight, 3)}`] : []),
    `source: ${yamlString(sourceName)}`,
    `sourceSha256: ${createHash('sha256').update(raw).digest('hex').slice(0, 12)}`,
    '---',
    '',
  ].join('\n');

  await writeFile(path.join(outDir, fileOf(chapter.order)), `${frontmatter}${body.trim()}\n`, 'utf8');
  written += 1;
}

// ── 报告 ────────────────────────────────────────────────────────────────

console.log(`[import] 专栏 ${SERIES_ID} · 章节 ${written} 篇 → src/content/posts/${SERIES_ID}-*.md`);
console.log(
  `[import] 内网标识改写：${
    redactionCounts.size === 0
      ? '无命中'
      : [...redactionCounts].map(([label, count]) => `${label}×${count}`).join(' · ')
  }`,
);

if (unresolvedAll.length > 0) {
  console.log(`[import] 未解析的章节引用 ${unresolvedAll.length} 处（已降级为纯文本，不进链接）：`);
  for (const item of unresolvedAll) console.log(`  第 ${item.chapter} 章  「${item.label}」→ ${item.target}`);
}

if (leftoversAll.length > 0) {
  console.log(`[import] WARN 仍有 ${leftoversAll.length} 处相对文档引用没改写（相对链接进不了链接检查，会变线上死链）：`);
  for (const item of leftoversAll) console.log(`  第 ${item.chapter} 章  ${item.target}`);
}

if (fenceOdd.length > 0) {
  console.log(`[import] 代码围栏不配对 ${fenceOdd.length} 篇（源头少写一个开 \`\`\`，会让紧随的正文被当成代码块）：`);
  for (const item of fenceOdd) {
    console.log(`  ✗ 第 ${item.chapter} 章 ${item.slug}（\`\`\` 行数 ${item.count}）`);
  }
  console.log('  处理方式：在源文档对应位置补上开围栏后重跑导入；导入不改写源文档。');
}

console.log('[import] 下一步：npm run build（脱敏 / 专栏 / 链接三道闸门会复核导入产物）');
