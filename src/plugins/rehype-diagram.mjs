/**
 * rehype 插件：把 Markdown 里的图表引用就地替换为内联 SVG。
 *
 * 写法（Markdown 内容侧）：
 *   ![图注文字](diagram:flow-site-pipeline)
 *
 * 产物为内联 SVG：无额外请求、无客户端 Mermaid 运行时、禁 JS 也能看。
 * 这样"新增内容 = 加一个 md 文件"成立，作者无需接触任何组件。
 */
import { JSDOM } from 'jsdom';
import { loadDiagramSvg, isValidDiagramName } from '../utils/diagram.mjs';

const PREFIX = 'diagram:';

/** DOM 节点 → hast 节点（只处理元素与文本，忽略注释） */
function domToHast(node) {
  if (node.nodeType === 3) {
    const value = node.nodeValue ?? '';
    return value.trim() ? { type: 'text', value } : null;
  }
  if (node.nodeType !== 1) return null;

  const properties = {};
  for (const attr of node.attributes) {
    if (attr.name === 'class') {
      properties.className = attr.value.split(/\s+/).filter(Boolean);
    } else {
      properties[attr.name] = attr.value;
    }
  }

  const children = [...node.childNodes].map(domToHast).filter(Boolean);

  return {
    type: 'element',
    tagName: node.tagName.toLowerCase(),
    properties,
    children,
  };
}

/** 取 SVG 源码并转成 hast 子树 */
function buildFigure(name, caption) {
  const svg = loadDiagramSvg(name);
  const dom = new JSDOM(svg, { contentType: 'image/svg+xml' });
  const svgElement = domToHast(dom.window.document.documentElement);

  const children = [svgElement];
  if (caption) {
    children.push({
      type: 'element',
      tagName: 'figcaption',
      properties: {},
      children: [{ type: 'text', value: caption }],
    });
  }

  return {
    type: 'element',
    tagName: 'figure',
    properties: { className: ['diagram'] },
    children,
  };
}

export default function rehypeDiagram() {
  return (tree) => {
    const walk = (parent) => {
      if (!Array.isArray(parent.children)) return;

      parent.children = parent.children.map((child) => {
        if (
          child.type === 'element' &&
          child.tagName === 'img' &&
          typeof child.properties?.src === 'string' &&
          child.properties.src.startsWith(PREFIX)
        ) {
          const name = child.properties.src.slice(PREFIX.length).trim();
          if (!isValidDiagramName(name)) {
            throw new Error(
              `图表引用不合法："${name}"。名称只允许小写字母、数字与连字符，例如 diagram:arch-overview`,
            );
          }
          return buildFigure(name, child.properties.alt ?? '');
        }

        walk(child);
        return child;
      });
    };

    walk(tree);
  };
}
