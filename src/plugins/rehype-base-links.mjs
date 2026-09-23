/**
 * rehype 插件：给内容里的站内绝对链接补上托管前缀。
 *
 * 作者在 Markdown 里一律写根路径（`/posts/xxx`、`/claudecode/03-query-engine`），
 * 插件在构建期按 BASE_PATH 补前缀（`/sub/posts/xxx`）。这样：
 * - 内容侧不用关心托管在根路径还是子路径，换托管/绑域名都不用改内容
 * - 子路径托管下站内互链不会 404（Astro 只给 HTML 里用 withBase 写的链接补前缀，
 *   内容里的裸路径它管不着）
 *
 * 只处理站内绝对链接：外链、锚点、mailto 一律不碰。
 */
const BASE = (import.meta.env?.BASE_URL ?? process.env.BASE_PATH ?? '/').replace(/\/$/, '');

/** 是否需要改写：站内绝对路径且尚未带前缀 */
function needsPrefix(href) {
  if (typeof href !== 'string' || !href.startsWith('/')) return false;
  if (href.startsWith('//')) return false; // 协议相对的外链
  if (href.startsWith(`${BASE}/`) || href === BASE) return false;
  return true;
}

export default function rehypeBaseLinks() {
  const prefix = BASE;

  return (tree) => {
    if (!prefix) return; // 根路径托管：不用改

    const walk = (node) => {
      if (!Array.isArray(node.children)) return;

      for (const child of node.children) {
        if (child.type === 'element' && child.tagName === 'a') {
          const href = child.properties?.href;
          if (needsPrefix(href)) {
            child.properties.href = `${prefix}${href}`;
          }
        }
        walk(child);
      }
    };

    walk(tree);
  };
}
