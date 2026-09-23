/**
 * 图表加载 —— 读取构建期生成的 SVG，供 Markdown 与 Astro 组件两侧共用。
 * 生成物位置：src/assets/diagrams/<name>.svg（由 npm run diagrams 产出）
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { projectPath } from './paths.mjs';

const diagramsDir = projectPath('src', 'assets', 'diagrams');

/**
 * 按名读取图表 SVG（内联用）。
 * @param {string} name 图源文件名（不含扩展名），如 flow-site-pipeline
 * @returns {string} 已去除生成标记注释的 SVG 源码
 */
export function loadDiagramSvg(name) {
  const file = path.join(diagramsDir, `${name}.svg`);
  let svg;
  try {
    svg = readFileSync(file, 'utf8');
  } catch {
    throw new Error(
      `图表 "${name}" 不存在（期望 ${file}）。请先执行 npm run diagrams 生成 SVG。`,
    );
  }
  // 去掉生成标记注释：内联进页面时不需要这段提示
  return svg.replace(/^<!--[\s\S]*?-->\s*/, '');
}

/** 图表名称是否合法（防路径穿越） */
export function isValidDiagramName(name) {
  return /^[a-z0-9][a-z0-9-]*$/.test(name);
}
