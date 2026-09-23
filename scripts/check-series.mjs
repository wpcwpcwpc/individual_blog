/**
 * 专栏一致性校验 —— 构建前置硬闸门。
 *
 * 专栏元数据的唯一定义处是 src/series.ts（标题 / 描述 / 分区顺序），
 * 文章 frontmatter 只声明"这章属于哪个专栏、哪个分区、第几章"。
 * 本脚本逐篇比对两处，不一致即失败 —— 避免出现"无主章节""幽灵分区"，
 * 或专区页因序号重复而悄悄少列一章。
 *
 * 章节编号缺失只提示、不拦构建（源材料本身就有空号，缺号不等于出错）。
 */
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { SERIES } from '../src/series.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const postsDir = path.join(root, 'src', 'content', 'posts');

/** 取出 markdown 的 frontmatter 文本 */
function frontmatterOf(text) {
  const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  return match ? match[1] : '';
}

/** 解析 frontmatter 中的标量字段（专栏字段都是单行标量，不需要完整 YAML） */
function scalarOf(frontmatter, key) {
  const value = frontmatter.match(new RegExp(`^${key}:\\s*(.+)$`, 'm'))?.[1]?.trim();
  if (value === undefined) return undefined;
  return value.replace(/^["']|["']$/g, '');
}

const entries = await readdir(postsDir, { withFileTypes: true }).catch(() => []);
const files = entries
  .filter((entry) => entry.isFile() && entry.name.endsWith('.md'))
  .map((entry) => path.join(postsDir, entry.name));

const failures = [];
const perSeries = new Map(); // 专栏 id → [{ file, order, group }]
let chapters = 0;

for (const file of files) {
  const frontmatter = frontmatterOf(await readFile(file, 'utf8'));
  const seriesId = scalarOf(frontmatter, 'series');
  if (!seriesId) continue;

  chapters += 1;
  const where = path.relative(root, file);
  const series = SERIES.find((item) => item.id === seriesId);

  if (!series) {
    failures.push(
      `  ${where}\n    声明了未登记的专栏「${seriesId}」—— 先在 src/series.ts 登记，或改掉 frontmatter`,
    );
    continue;
  }

  const rawOrder = scalarOf(frontmatter, 'seriesOrder');
  const order = rawOrder === undefined ? NaN : Number(rawOrder);
  if (!Number.isFinite(order)) {
    failures.push(`  ${where}\n    缺 seriesOrder（章节序号）—— 专区页靠它排序`);
  }

  const group = scalarOf(frontmatter, 'seriesGroup');
  if (!group) {
    failures.push(`  ${where}\n    缺 seriesGroup（分区名）`);
  } else if (!series.groups.some((item) => item.name === group)) {
    failures.push(
      `  ${where}\n    分区「${group}」不在专栏登记表里 —— 可用分区：${series.groups.map((g) => g.name).join(' / ')}`,
    );
  }

  if (!perSeries.has(seriesId)) perSeries.set(seriesId, []);
  perSeries.get(seriesId).push({ file: where, order, group });
}

for (const [seriesId, list] of perSeries) {
  const byOrder = new Map();
  for (const item of list) {
    if (!Number.isFinite(item.order)) continue;
    if (byOrder.has(item.order)) {
      failures.push(
        `  ${seriesId} 第 ${item.order} 章重复：${byOrder.get(item.order)} 与 ${item.file}\n    同一专栏内章节序号必须唯一`,
      );
    } else {
      byOrder.set(item.order, item.file);
    }
  }

  const orders = [...byOrder.keys()].sort((a, b) => a - b);
  const gaps = [];
  for (let i = 1; i < orders.length; i += 1) {
    if (orders[i] - orders[i - 1] > 1) {
      gaps.push(`${orders[i - 1] + 1}–${orders[i] - 1}`);
    }
  }
  if (gaps.length > 0) {
    console.log(`[series] ${seriesId} 章节编号有空号（源材料如此）：${gaps.join('、')}`);
  }
}

console.log(
  `[series] 专栏 ${perSeries.size} 个 · 章节 ${chapters} 篇 · 登记分区 ${SERIES.reduce(
    (total, series) => total + series.groups.length,
    0,
  )} 个`,
);

if (failures.length > 0) {
  console.error(`\n[series] FAIL 专栏元数据不一致，构建中止：\n\n${failures.join('\n\n')}\n`);
  console.error('  处理方式：以 src/series.ts 为准，修正文章 frontmatter 的专栏字段。');
  process.exit(1);
}

console.log('[series] PASS 章节与专栏登记一致');
