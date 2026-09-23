import type { APIRoute } from 'astro';

// robots.txt 由端点生成：sitemap 地址跟随构建期站点 URL 与资源前缀，不写死域名。
// 注意子路径部署时 sitemap 也在前缀之下，故需带上 BASE_URL。
export const GET: APIRoute = ({ site, url }) => {
  const origin = site ?? url;
  const prefix = import.meta.env.BASE_URL.replace(/\/$/, '');
  const sitemap = new URL(`${prefix}/sitemap-index.xml`, origin).href;

  const body = ['User-agent: *', 'Allow: /', '', `Sitemap: ${sitemap}`, ''].join('\n');

  return new Response(body, {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
};
