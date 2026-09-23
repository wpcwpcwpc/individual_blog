/**
 * 图表生成 —— diagrams/*.mmd（Mermaid 源）→ src/assets/diagrams/*.svg
 *
 * 构建期渲染，产物为内联 SVG：站点不依赖客户端 Mermaid 运行时。
 * 产出的 SVG 是生成物，禁止手改（下次构建会覆盖）。
 *
 * Node 下渲染 Mermaid 需要 DOM：用 jsdom 提供，并补上 SVG 文本量测
 * （jsdom 无布局引擎，按字符宽度估算，CJK 按 1em、拉丁按 0.55em）。
 */
import { readFile, writeFile, readdir, mkdir, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { JSDOM } from 'jsdom';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const srcDir = path.join(root, 'diagrams');
const outDir = path.join(root, 'src', 'assets', 'diagrams');

const FONT_STACK =
  'system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif';

/** 用 jsdom 搭一个最小 DOM 环境，供 Mermaid 渲染使用 */
function setupDom() {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    pretendToBeVisual: true,
    url: 'http://localhost/',
  });

  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  Object.defineProperty(globalThis, 'navigator', {
    value: dom.window.navigator,
    configurable: true,
  });

  // Mermaid 直接引用浏览器全局（CSSStyleSheet、Element 等），逐个桥接过来
  const bridgeKeys = [
    'CSSStyleSheet',
    'CSSRule',
    'CSSMediaRule',
    'CSSImportRule',
    'Element',
    'Node',
    'DocumentFragment',
    'Text',
    'HTMLElement',
    'SVGElement',
    'Event',
    'CustomEvent',
    'Image',
    'DOMParser',
    'XMLSerializer',
    'MutationObserver',
  ];
  for (const key of bridgeKeys) {
    const value = dom.window[key];
    if (value === undefined || globalThis[key] !== undefined) continue;
    try {
      globalThis[key] = value;
    } catch {
      // 只读全局，跳过
    }
  }

  if (typeof globalThis.getComputedStyle !== 'function') {
    globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
  }
  if (typeof globalThis.requestAnimationFrame !== 'function') {
    globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window);
  }
  // jsdom 未实现的观测类接口：Mermaid 只在交互场景使用，静态渲染给空实现即可
  if (typeof globalThis.ResizeObserver === 'undefined') {
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    globalThis.IntersectionObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }

  // jsdom 不实现 SVG 几何量测：按字符估算。系数贴近真实字体度量（CJK 约 1em、拉丁约 0.56em），
  // 估得太窄文字压边，估得太宽会被 Mermaid 判为超宽而换行——两者都要避免
  const proto = dom.window.SVGElement.prototype;
  proto.getBBox = function getBBox() {
    const text = (this.textContent ?? '').trim();
    const fontSize = Number.parseFloat(this.getAttribute?.('font-size') ?? '') || 16;
    const width = [...text].reduce(
      (sum, char) => sum + (char.codePointAt(0) > 0x2e80 ? fontSize * 1.02 : fontSize * 0.62),
      0,
    );
    return { x: 0, y: 0, width: Math.max(8, width + 4), height: fontSize * 1.35 };
  };
  proto.getComputedTextLength = function getComputedTextLength() {
    return this.getBBox().width;
  };
  proto.getScreenCTM = function getScreenCTM() {
    return { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0, inverse: () => this, multiply: () => this };
  };

  return dom;
}

/** 读取 .mmd 头部注释里的用途说明，作为 SVG 的无障碍描述 */
function describe(source, fileName) {
  const note = source
    .split('\n')
    .filter((line) => line.trim().startsWith('%%'))
    .find((line) => line.includes('@desc'));
  return note ? note.split('@desc')[1].trim() : fileName;
}

/**
 * jsdom 没有布局引擎，Mermaid 依此算出的视口会退化（把整段内联 CSS 当成文本宽度）。
 * 这里改为从渲染结果反推真实包围盒：节点形状（rect/circle/polygon）+ 连线路径
 * （解析 d 的坐标命令）+ 边标签位移，取并集后加内边距写入 viewBox。
 */
function computeViewBox(svgString, padding = 8) {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  // H/V 命令只给出单轴坐标，先单独累积，最后与主包围盒合并
  const horizontal = [];
  const vertical = [];

  const doc = new dom.window.DOMParser().parseFromString(svgString, 'image/svg+xml');
  if (doc.querySelector('parsererror')) return null;

  const add = (x1, y1, x2, y2) => {
    // 任一坐标非有限值（解析失败 / 缺属性）时整点丢弃，避免 NaN 污染整个包围盒
    if (![x1, y1, x2, y2].every(Number.isFinite)) return;
    minX = Math.min(minX, x1);
    minY = Math.min(minY, y1);
    maxX = Math.max(maxX, x2);
    maxY = Math.max(maxY, y2);
  };

  /** 累加元素自身的 translate 位移 */
  const translateOf = (element) => {
    const transform = element.getAttribute?.('transform') ?? '';
    const match = transform.match(/translate\(\s*(-?[\d.]+)[,\s]+(-?[\d.]+)?/);
    if (!match) return { x: 0, y: 0 };
    return { x: Number(match[1]), y: Number(match[2] ?? 0) };
  };

  /** 累积从 svg 根到该元素的所有 translate */
  const absoluteOffset = (element) => {
    let x = 0;
    let y = 0;
    let node = element;
    while (node && node.tagName?.toLowerCase() !== 'svg') {
      const offset = translateOf(node);
      x += offset.x;
      y += offset.y;
      node = node.parentElement;
    }
    return { x, y };
  };

  for (const shape of doc.querySelectorAll('rect, circle, polygon, line')) {
    // 跳过画布背景与图例色块（无意义的整幅矩形会把视口撑满）
    const cls = shape.getAttribute('class') ?? '';
    if (cls.includes('background')) continue;

    const { x: ox, y: oy } = absoluteOffset(shape);
    const tag = shape.tagName.toLowerCase();

    if (tag === 'rect') {
      const w = Number(shape.getAttribute('width') ?? 0);
      const h = Number(shape.getAttribute('height') ?? 0);
      if (!w || !h) continue;
      const x = Number(shape.getAttribute('x') ?? 0) + ox;
      const y = Number(shape.getAttribute('y') ?? 0) + oy;
      add(x, y, x + w, y + h);
    } else if (tag === 'circle') {
      const r = Number(shape.getAttribute('r') ?? 0);
      if (!r) continue;
      const cx = Number(shape.getAttribute('cx') ?? 0) + ox;
      const cy = Number(shape.getAttribute('cy') ?? 0) + oy;
      add(cx - r, cy - r, cx + r, cy + r);
    } else if (tag === 'line') {
      // 时序图的 lifeline 与消息线是 <line>，不参与会把视口裁掉一截
      const x1 = Number(shape.getAttribute('x1'));
      const y1 = Number(shape.getAttribute('y1'));
      const x2 = Number(shape.getAttribute('x2'));
      const y2 = Number(shape.getAttribute('y2'));
      add(
        Math.min(x1, x2) + ox, Math.min(y1, y2) + oy,
        Math.max(x1, x2) + ox, Math.max(y1, y2) + oy,
      );
    } else {
      const pairs = (shape.getAttribute('points') ?? '').trim().split(/\s+/);
      for (const pair of pairs) {
        const [px, py] = pair.split(',').map(Number);
        if (Number.isFinite(px) && Number.isFinite(py)) add(px + ox, py + oy, px + ox, py + oy);
      }
    }
  }

  // 连线：按 SVG path 命令逐个取坐标点（绝对坐标），忽略 Z 与相对命令
  const tokenPattern = /([MLCQTSAHVZmlcqtsahvz])|(-?\d*\.?\d+(?:e-?\d+)?)/g;
  const pairCount = { M: 1, L: 1, T: 1, C: 3, S: 2, Q: 2, A: 7 };
  for (const pathElement of doc.querySelectorAll('path')) {
    const d = pathElement.getAttribute('d') ?? '';
    if (!d) continue;
    const { x: ox, y: oy } = absoluteOffset(pathElement);

    let command = null;
    let buffer = [];
    const flush = () => {
      if (!command || buffer.length === 0) return;
      const arity = pairCount[command.toUpperCase()];
      if (command === 'H') {
        for (const value of buffer) horizontal.push(value + ox);
      } else if (command === 'V') {
        for (const value of buffer) vertical.push(value + oy);
      } else if (arity) {
        for (let i = 0; i + 1 < buffer.length; i += arity) {
          const x = buffer[i + arity - 2];
          const y = buffer[i + arity - 1];
          add(x + ox, y + oy, x + ox, y + oy);
        }
      }
      buffer = [];
    };

    for (const token of d.matchAll(tokenPattern)) {
      if (token[1]) {
        flush();
        command = token[1];
      } else {
        buffer.push(Number(token[2]));
      }
    }
    flush();
  }

  // 边标签是独立的 <text>，不带形状：不并入包围盒时会被视口裁掉（"越权或危险调用"只露半个字）。
  // 文本宽度沿用与 getBBox 同一套字符估算，按 text-anchor 决定向左还是向右展开。
  for (const text of doc.querySelectorAll('text')) {
    const content = (text.textContent ?? '').trim();
    if (!content) continue;
    const fontSize = Number.parseFloat(text.getAttribute('font-size') ?? '') || 16;
    const width = [...content].reduce(
      (sum, char) => sum + (char.codePointAt(0) > 0x2e80 ? fontSize * 1.02 : fontSize * 0.62),
      0,
    );
    const height = fontSize * 1.35;
    const { x: ox, y: oy } = absoluteOffset(text);
    const x = Number(text.getAttribute('x') ?? 0) + ox;
    const y = Number(text.getAttribute('y') ?? 0) + oy;
    const anchor =
      text.getAttribute('text-anchor') ??
      (/text-anchor:\s*middle/.test(text.getAttribute('style') ?? '') ? 'middle' : 'start');
    const left = anchor === 'middle' ? x - width / 2 : x;
    const right = anchor === 'middle' ? x + width / 2 : x + width;
    add(left, y - height * 0.8, right, y + height * 0.3);
  }

  // 把单轴坐标并回包围盒：横轴值落在当前纵向范围内，纵轴值同理
  if (Number.isFinite(minY) && Number.isFinite(maxY)) {
    for (const x of horizontal) add(x, minY, x, maxY);
  }
  if (Number.isFinite(minX) && Number.isFinite(maxX)) {
    for (const y of vertical) add(minX, y, maxX, y);
  }

  if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null;

  const width = Math.max(1, maxX - minX) + padding * 2;
  const height = Math.max(1, maxY - minY) + padding * 2;
  return `${(minX - padding).toFixed(1)} ${(minY - padding).toFixed(1)} ${width.toFixed(1)} ${height.toFixed(1)}`;
}

const dom = setupDom();
const mermaid = (await import('mermaid')).default;

mermaid.initialize({
  startOnLoad: false,
  securityLevel: 'strict',
  theme: 'neutral',
  fontFamily: FONT_STACK,
  // 关掉它，标签才是 <text> 而非 <foreignObject>：可被 PDF / 邮件 / 各类渲染器正确呈现
  htmlLabels: false,
  // 间距给足：Mermaid 交给布局的宽度小于它实际绘制的节点框宽度，
  // 默认 50 会让并排节点互相压盖（同排节点越多的图越明显）
  flowchart: { useMaxWidth: true, wrappingWidth: 480, nodeSpacing: 90, rankSpacing: 90 },
});

let files = [];
try {
  files = (await readdir(srcDir)).filter((name) => name.endsWith('.mmd')).sort();
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}

if (files.length === 0) {
  console.log('[diagrams] diagrams/ 下暂无 .mmd 源文件，跳过');
  process.exit(0);
}

await rm(outDir, { recursive: true, force: true });
await mkdir(outDir, { recursive: true });

for (const file of files) {
  const name = path.basename(file, '.mmd');
  const source = await readFile(path.join(srcDir, file), 'utf8');
  const id = `diagram-${name}`;

  let svg;
  try {
    ({ svg } = await mermaid.render(id, source, document.body));
  } catch (error) {
    console.error(`[diagrams] FAIL ${file} 渲染失败：${error.message}`);
    process.exit(1);
  }

  const header = `<!-- 生成物，请勿手改：源文件 diagrams/${file}，执行 npm run diagrams 重新生成 -->\n`;
  const label = describe(source, name).replace(/"/g, '&quot;');
  const viewBox = computeViewBox(svg);
  if (!viewBox) {
    console.warn(`[diagrams] WARN ${file} 无法推算视口，沿用 Mermaid 输出（可能留白异常）`);
  }

  const patched = svg
    // 交给 CSS 控制尺寸，保留 viewBox 以便等比缩放
    .replace(/<svg([^>]*?)style="[^"]*"/, '<svg$1')
    .replace(/<svg([^>]*?)width="[^"]*"/, '<svg$1')
    .replace(/<svg([^>]*?)height="[^"]*"/, '<svg$1')
    // Mermaid 自带的 role/aria-label 会与新加的重复（XML 属性不可重复），先移除
    .replace(/\s+role="[^"]*"/g, '')
    .replace(/\s+aria-label="[^"]*"/g, '')
    .replace(/viewBox="[^"]*"/, viewBox ? `viewBox="${viewBox}"` : '')
    .replace('<svg', `<svg role="img" aria-label="${label}"`);

  await writeFile(path.join(outDir, `${name}.svg`), header + patched, 'utf8');
  console.log(`[diagrams] ${file} → src/assets/diagrams/${name}.svg`);
}

console.log(`[diagrams] 完成 ${files.length} 张`);
