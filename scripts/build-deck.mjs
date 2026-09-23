/**
 * deck 构建 —— `deck/*` + 站点图源 → `public/deck/qa-agent/index.html`（单文件自包含）
 *
 * 与站点解耦：deck 只是一个静态产物，站点不依赖本脚本的产物；
 * 本脚本失败时只告警、退出码为 0，绝不阻塞项目页与文章的构建发布。
 *
 * 图源不复制：SVG 取自站点图表管线（`src/assets/diagrams/*.svg`），
 * 改一次 `.mmd`，站点与 deck 同步更新。
 */
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { loadDiagramSvg } from '../src/utils/diagram.mjs';
import { projectPath } from '../src/utils/paths.mjs';
import { slides } from '../deck/slides.mjs';

const DECK_SLUG = 'qa-agent';
const base = (process.env.BASE_PATH ?? '/').replace(/\/$/, '');

const escapeHtml = (text) =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** 内容里的 **粗体** 转 <strong>（其余按纯文本转义） */
const inline = (text) => escapeHtml(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

/** 读项目页 frontmatter 的 figures（与站点同一处登记，已由 check-figures 校验过一致性） */
async function readFigures() {
  const source = await readFile(projectPath('src', 'content', 'projects', `${DECK_SLUG}.md`), 'utf8');
  const block = source.match(/^---\r?\n([\s\S]*?)\r?\n---/)?.[1] ?? '';
  const figuresBlock = block.split(/^figures:\s*$/m)[1];
  if (!figuresBlock) return [];

  const figures = [];
  for (const chunk of figuresBlock.split(/^\s*-\s+/m).slice(1)) {
    const body = chunk.split(/\n\S/)[0];
    const pick = (key) => body.match(new RegExp(`^\\s*${key}:\\s*(.+)$`, 'm'))?.[1]?.trim();
    const label = pick('label');
    const value = pick('value')?.replace(/^["']|["']$/g, '');
    if (label && value) figures.push({ label, value });
  }
  return figures;
}

async function figureHtml(name, caption) {
  const svg = await loadDiagramSvg(name);
  return `<figure class="fig">${svg}${caption ? `<figcaption>${inline(caption)}</figcaption>` : ''}</figure>`;
}

async function renderSlide(slide, index, context) {
  const parts = [
    `<section class="slide slide--${slide.kind}" id="s${index + 1}">`,
    `<p class="slide__index">${String(index + 1).padStart(2, '0')} / ${String(slides.length).padStart(2, '0')}</p>`,
  ];

  if (slide.kind === 'cover') {
    parts.push(`<h1>${inline(slide.title)}</h1>`);
    if (slide.sub) parts.push(`<p class="slide__sub">${inline(slide.sub)}</p>`);
    parts.push(
      `<p class="meta">${escapeHtml(context.site.author)} · <a href="mailto:${context.site.email}">${escapeHtml(context.site.email)}</a> · <a href="${context.site.github}" rel="noopener">${escapeHtml(context.site.github.replace(/^https?:\/\//, ''))}</a></p>`,
    );
    parts.push('</section>');
    return parts.join('\n');
  }

  parts.push(`<h2>${inline(slide.title)}</h2>`);

  if (slide.sub) parts.push(`<p class="slide__sub">${inline(slide.sub)}</p>`);

  if (slide.kind === 'figures') {
    parts.push(
      `<dl class="figures">${context.figures
        .map((figure) => `<div class="figure"><dt>${inline(figure.label)}</dt><dd>${inline(figure.value)}</dd></div>`)
        .join('')}</dl>`,
    );
  }

  if (slide.figure) {
    parts.push(await figureHtml(slide.figure, slide.caption));
  }

  if (slide.kind === 'end') {
    parts.push(
      [
        '<ul class="points">',
        `<li>项目页：<a href="${base}/projects/${DECK_SLUG}">${escapeHtml(context.site.name)}的项目页</a>（取舍与难点）</li>`,
        `<li>深挖文章：<a href="${base}/posts/multi-agent-vs-one-agent">多 Agent 拆分</a> · <a href="${base}/posts/spec-driven-agent-evolution">规格驱动</a> · <a href="${base}/posts/three-guardrails">三道闸门</a></li>`,
        `<li>简历：<a href="${base}/resume">简历页</a></li>`,
        `<li>邮箱：<a href="mailto:${escapeHtml(context.site.email)}">${escapeHtml(context.site.email)}</a> · GitHub：<a href="${context.site.github}" rel="noopener">${escapeHtml(context.site.github.replace(/^https?:\/\//, ''))}</a></li>`,
        '</ul>',
      ].join('\n'),
    );
  } else if (slide.points?.length > 0) {
    parts.push(`<ul class="points">${slide.points.map((point) => `<li>${inline(point)}</li>`).join('')}</ul>`);
  }

  parts.push('</section>');
  return parts.join('\n');
}

async function main() {
  const { SITE } = await import('../src/site.config.ts');
  const context = { site: SITE, figures: await readFigures() };

  const theme = await readFile(path.join(projectPath('deck'), 'theme.css'), 'utf8');
  const nav = await readFile(path.join(projectPath('deck'), 'nav.js'), 'utf8');

  const body = [];
  for (const [index, slide] of slides.entries()) {
    body.push(await renderSlide(slide, index, context));
  }

  const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(SITE.name)} · 多 Agent 编排平台（扫读版）</title>
<meta name="description" content="12 页扫读版：问题、架构、关键机制与结果">
<meta name="robots" content="noindex">
<style>
${theme}
</style>
</head>
<body>
${body.join('\n')}
<div class="progress"><div class="progress__bar"></div></div>
<div class="pager">
  <button type="button" class="pager__prev" aria-label="上一页">↑</button>
  <span><span class="pager__current">1</span>/<span class="pager__total">${slides.length}</span></span>
  <button type="button" class="pager__next" aria-label="下一页">↓</button>
</div>
<script>
${nav}
</script>
</body>
</html>
`;

  const outDir = projectPath('public', 'deck', DECK_SLUG);
  await mkdir(outDir, { recursive: true });
  await writeFile(path.join(outDir, 'index.html'), html, 'utf8');
  console.log(`[deck] ${slides.length} 页 → public/deck/${DECK_SLUG}/index.html（自包含单文件）`);
}

main().catch((error) => {
  // 与 PDF 同策略：deck 是"锦上添花"，失败不阻塞站点发布，页面会显示"待补齐"
  console.warn(`[deck] 构建失败（不阻塞站点发布）：${error.message}`);
  process.exit(0);
});
