/**
 * 脱敏扫描 —— 构建前置硬闸门。
 *
 * 读取禁出词表（CI：secret GLOSSARY_WORDS；本地：gitignored 的 GLOSSARY.local.md）
 * 的「2.1 精确词 / 2.2 正则模式」，扫描站点内容来源与源码镜像区（`mirror/`，见
 * change add-qa-agent-source）；命中即打印位置并以非零码退出，使构建失败——漏网内容
 * 物理上无法被发布。
 *
 * 词表缺失或为空视为闸门失效，同样失败（不允许"扫描器空转"通过）。
 * 命中词默认打码：公开仓库的 CI 日志同样公开，不能把要藏的词写进日志。
 */
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/**
 * 词表存放处 —— 公开仓库 MUST NOT 登记内部标识，词表因此不在仓库里：
 * CI 由 secret 注入全文，本地读已 gitignore 的 `GLOSSARY.local.md`。
 */
const wordsPath = path.join(root, 'GLOSSARY.local.md');

/** 公开仓库的 CI 日志同样公开：默认打码，本地要看全文设 SANITIZE_REVEAL=1 */
const REVEAL = process.env.SANITIZE_REVEAL === '1';
const maskToken = (token) => (REVEAL ? token : `${token.slice(0, 2)}***（${token.length} 字符）`);

/** 硬跳过：词表与处置清单自身就是内部标识的集合，扫它们会让构建永远失败 */
const IGNORE = [
  'GLOSSARY.local.md',
  'SANITIZE-LIST.local.md',
  // 源码搬运台账：通篇真实模块路径与内网信息，本身就是"内部模块地图"。
  // 按 add-qa-agent-source 自己的规格（source-sanitization：含真实文件名的处置清单只存在于本地隔离文件），
  // 它 MUST NOT 入库 —— 与上面两份清单同构，故同样不进扫描面。
  // 注意：文件名保持不变（openspec CLI 靠 tasks.md 追踪任务），隔离靠 .gitignore + 此处双保险。
  'openspec/changes/add-qa-agent-source/tasks.md',
];

/**
 * 源码镜像区（`mirror/`）的扫描面与排除规则。
 * 排除项 MUST 显式声明 —— 不依赖"扩展名不匹配"这类隐式排除。
 */
const SOURCE_EXTENSIONS = [
  // 源码
  '.py', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs',
  // 配置与声明
  '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.spec', '.txt',
  // 样式与页面
  '.css', '.scss', '.html', '.svg',
  // 脚本与容器
  '.sh', '.bash', '.ps1', '.bat', '.cmd',
  // 文本类资产
  '.md', '.sql',
  // 环境示例（`.env.example` 的 extname 是 `.example`；真实 `.env` 由 IGNORE 与排除目录兜住）
  '.example',
];

/** 无扩展名文件白名单：容器构建文件、依赖清单、构建脚本 */
const SOURCE_NO_EXT = [
  'Dockerfile', 'Makefile', 'Procfile', 'Vagrantfile', 'Jenkinsfile',
  'requirements', 'LICENSE', 'NOTICE',
];

/** 排除目录：构建产物、嵌入运行时、依赖目录、缓存 */
const EXCLUDED_DIRS = new Set([
  'node_modules', '.git', 'dist', 'build', 'out', 'target',
  '__pycache__', '.venv', 'venv', 'site-packages', 'vendor',
  '.pytest_cache', '.mypy_cache', '.ruff_cache', '.skill-cache', '.cache',
  'htmlcov', 'coverage', '.next', '.astro', '.turbo', '.nuxt',
]);

/** 排除扩展名：二进制与媒体（矢量图 `.svg` 按文本扫描——成本低，泄露面大） */
const EXCLUDED_EXTENSIONS = new Set([
  // 位图
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.bmp', '.tiff',
  // 字体
  '.woff', '.woff2', '.ttf', '.otf', '.eot',
  // 归档与产物
  '.pdf', '.zip', '.gz', '.tar', '.tgz', '.7z', '.rar',
  // 二进制
  '.exe', '.dll', '.so', '.dylib', '.bin', '.pyc', '.pyo', '.class', '.jar', '.node',
  // 音视频
  '.mp3', '.mp4', '.mov', '.avi', '.wav', '.flac', '.ogg', '.webm',
  // 数据库文件
  '.db', '.sqlite', '.sqlite3',
]);

/** 路径中任一层命中排除目录即跳过 */
function isExcludedPath(rel) {
  return rel.split('/').some((segment) => EXCLUDED_DIRS.has(segment));
}

/**
 * 扫描目标：内容、页面与组件的源码、图表源，以及仓库里的说明文档。
 * 规则文档同样公开 —— 早期禁出词表就是写在 `GLOSSARY.md` 与 `openspec/` 里漏出去的，
 * 所以这几处必须在范围内（根目录只扫一层，避免递归进 node_modules）。
 */
const scanRoots = [
  { dir: path.join(root, 'src'), extensions: ['.astro', '.ts', '.md', '.mdx', '.json'] },
  { dir: path.join(root, 'diagrams'), extensions: ['.mmd'] },
  // deck 的内容与样式直接进公开产物，必须与站点内容同一套闸门
  { dir: path.join(root, 'deck'), extensions: ['.mjs', '.js', '.css', '.md'] },
  { dir: root, extensions: ['.md'], deep: false },
  { dir: path.join(root, 'openspec'), extensions: ['.md', '.yaml', '.yml'] },
  // 源码镜像区：不进构建产物，但它进公开仓库——同样必须过闸门
  { dir: path.join(root, 'mirror'), extensions: SOURCE_EXTENSIONS, noExt: SOURCE_NO_EXT },
];

/**
 * 豁免清单 —— 经作者确认需要按原文公开的对外文档。
 * 简历是主动投递的对外材料：雇主名称、内部产品名、个人经历本来就随简历对外。
 * 豁免不等于不检查：命中仍然打印告警，让"哪些内部标识被公开了"始终可见，只是不拦构建。
 */
const EXEMPT = [
  {
    path: 'src/pages/resume/pdf.astro',
    reason: '简历 PDF 查看器：只承载 PDF 容器，无履历文本；保留豁免以防文本回归（见 RESUME.md）',
  },
];

function exemptionFor(filePath) {
  const rel = path.relative(root, filePath).replace(/\\/g, '/');
  return EXEMPT.find((item) => item.path === rel);
}

/** 从词表中取出 2.1 精确词（``` 代码块）与 2.2 正则模式（表格首列）。 */
function parseGlossary(text) {
  // 注意：不能用 '---' 做分隔——markdown 表格分隔行 |---|---| 里也含 '---'
  const sectionTwo = text.split('## 二、禁出词表')[1]?.split('\n## ')[0] ?? '';

  const exactSection = sectionTwo.split('### 2.1 精确词')[1]?.split('###')[0] ?? '';
  const seen = new Set();
  const exact = [];
  for (const line of (exactSection.match(/```[\s\S]*?```/)?.[0] ?? '').replace(/```/g, '').split('\n')) {
    const word = line.trim();
    const key = word.toLowerCase();
    if (!word || seen.has(key)) continue; // 大小写变体只保留一条，避免同一处重复上报
    seen.add(key);
    exact.push(word);
  }

  const regexSection = sectionTwo.split('### 2.2 正则模式')[1] ?? '';
  const patterns = [];
  for (const line of regexSection.split('\n')) {
    const match = line.match(/^\|\s*`(.+?)`\s*\|/);
    if (!match) continue;
    // 表格内的 \| 是 markdown 转义，还原为真正的正则或运算
    patterns.push(match[1].replace(/\\\|/g, '|'));
  }

  return { exact, patterns };
}

/** 递归收集扫描目标文件 */
async function collectFiles() {
  const files = [];
  for (const { dir, extensions, noExt = [], deep = true } of scanRoots) {
    let entries = [];
    try {
      entries = await readdir(dir, { recursive: deep, withFileTypes: true });
    } catch (error) {
      if (error.code === 'ENOENT') continue; // 目录尚未创建，跳过
      throw error;
    }
    for (const entry of entries) {
      if (!entry.isFile()) continue;
      const full = path.join(entry.parentPath ?? entry.path, entry.name);
      const rel = path.relative(root, full).replace(/\\/g, '/');
      if (IGNORE.includes(rel)) continue;
      if (isExcludedPath(rel)) continue;

      const ext = path.extname(entry.name).toLowerCase();
      if (EXCLUDED_EXTENSIONS.has(ext)) continue;
      if (extensions.includes(ext)) {
        files.push(full);
        continue;
      }
      // 无扩展名文件只按显式白名单收，避免把杂项文本整片拖进扫描面
      if (!ext && noExt.includes(entry.name)) files.push(full);
    }
  }
  return files;
}

/** 对单个文件做精确词与正则匹配，返回命中列表 */
function scanText(text, exact, patterns) {
  const hits = [];
  const lower = text.toLowerCase();

  for (const word of exact) {
    const needle = word.toLowerCase();
    let index = lower.indexOf(needle);
    while (index !== -1) {
      hits.push({ token: word, index });
      index = lower.indexOf(needle, index + needle.length);
    }
  }

  for (const source of patterns) {
    const re = new RegExp(source, 'gi');
    for (const match of text.matchAll(re)) {
      hits.push({ token: match[0], index: match.index ?? 0 });
    }
  }

  return hits;
}

function lineOf(text, index) {
  return text.slice(0, index).split('\n').length;
}

const inlineWords = process.env.GLOSSARY_WORDS?.trim();
const glossary = inlineWords || (await readFile(wordsPath, 'utf8').catch(() => null));

if (!glossary) {
  console.error(
    '[sanitize] FAIL 取不到禁出词表 —— 闸门失效，构建中止\n' +
      `  本地：把词表写到 ${path.relative(root, wordsPath)}（不入库）\n` +
      '  CI：在仓库 secret 里配置 GLOSSARY_WORDS（值为词表全文）\n' +
      '  词表形态见 GLOSSARY.md 第一节',
  );
  process.exit(1);
}

const { exact, patterns } = parseGlossary(glossary);

if (exact.length === 0) {
  console.error('[sanitize] FAIL 词表的「2.1 精确词」为空 —— 闸门失效，构建中止');
  process.exit(1);
}

if (patterns.length === 0) {
  console.error('[sanitize] FAIL 词表的「2.2 正则模式」为空 —— 闸门失效，构建中止');
  process.exit(1);
}

const files = await collectFiles();
const reports = [];
const warnings = [];
let exemptCount = 0;

for (const file of files) {
  const text = await readFile(file, 'utf8');
  const hits = scanText(text, exact, patterns);
  if (hits.length === 0) continue;

  const lines = hits
    .sort((a, b) => a.index - b.index)
    .map((hit) => `    第 ${lineOf(text, hit.index)} 行  命中「${maskToken(hit.token)}」`);

  const exempt = exemptionFor(file);
  const body = `  ${path.relative(root, file)}${exempt ? `（已豁免：${exempt.reason}）` : ''}\n${lines.join('\n')}`;

  if (exempt) {
    exemptCount += 1;
    warnings.push(body);
  } else {
    reports.push(body);
  }
}

console.log(
  `[sanitize] 扫描 ${files.length} 个文件 · 精确词 ${exact.length} 条 · 正则 ${patterns.length} 条 · 豁免 ${exemptCount} 个 · 排除目录 ${EXCLUDED_DIRS.size} 项 / 排除扩展 ${EXCLUDED_EXTENSIONS.size} 项 · 词表来源=${inlineWords ? 'CI secret' : '本地文件'}`,
);

if (warnings.length > 0) {
  console.warn(
    `\n[sanitize] WARN 已豁免文件命中禁出词（不拦构建，但请确认这些都是可公开信息）：\n\n${warnings.join('\n\n')}\n`,
  );
}

if (reports.length > 0) {
  console.error(`\n[sanitize] FAIL 命中禁出词，构建中止：\n\n${reports.join('\n\n')}\n`);
  console.error('  处理方式：按词表第一节映射表替换为对外泛称，或区间化数字后重试。');
  console.error('  查看命中原文：本地设 SANITIZE_REVEAL=1 重跑。');
  process.exit(1);
}

console.log('[sanitize] PASS 未命中禁出词');
