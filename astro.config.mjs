import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import { unified } from '@astrojs/markdown-remark';
import rehypeDiagram from './src/plugins/rehype-diagram.mjs';
import rehypeBaseLinks from './src/plugins/rehype-base-links.mjs';

// 站点 URL 与资源路径前缀均通过环境变量注入，换域名 / 换托管 / 切子目录时无需改源码。
// SITE_URL  例：https://yourname.dev
// BASE_PATH 例：/ （根路径）或 /some-sub/ （子路径托管）
const site = process.env.SITE_URL ?? 'https://example.com';
const base = process.env.BASE_PATH ?? '/';

export default defineConfig({
  site,
  base,
  output: 'static',
  trailingSlash: 'ignore',
  integrations: [sitemap()],
  markdown: {
    // Astro 7 默认处理器是 Sätteri；插件需要走 unified 处理器：
    // rehypeDiagram 把 ![图注](diagram:名称) 就地内联成 SVG；
    // rehypeBaseLinks 给内容里的站内绝对链接补托管前缀（子路径托管时不 404）
    processor: unified({ rehypePlugins: [rehypeDiagram, rehypeBaseLinks] }),
  },
  build: {
    inlineStylesheets: 'auto',
  },
});
