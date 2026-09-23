/**
 * 链接与资源健康检查 —— 扫描 dist/ 全部 HTML，验证站内链接与静态资源真实存在。
 *
 * 覆盖两类问题：
 * 1. 目标文件缺失 —— 导航链接、文章/项目互链、样式与脚本引用、图片与 SVG、favicon、OG 图、PDF 入口。
 * 2. 托管前缀失配 —— 根绝对路径没带前缀，或指向本站之外的 *.github.io（改名/换托管留下的旧域）。
 *    这类链接在 dist 里能找到对应文件（第 1 类检查会放行），上线却必然 404：
 *    它们出自绕开 Astro 前缀管线的产物（deck、PDF），或来自改名前的历史残留。
 *
 * 任一类命中即非零退出，用于发布前把关（避免上线后 404）。
 *
 * 用法：
 *   node scripts/check-links.mjs            # 前缀与站点源自动探测（读 dist 的 sitemap），也可用 BASE_PATH/SITE_URL 显式指定
 *   BASE_PATH=/sub/ node scripts/check-links.mjs
 */
import { readdir, readFile, stat } from 'node:fs/promises';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const distDir = path.join(root, 'dist');

/**
 * 前缀与站点源优先取环境变量；没传就从 sitemap 里的 URL 反推。
 * 否则子路径构建会被误判成"满站死链"——那是构建前缀与检查前缀不一致，不是站点坏了。
 */
function resolveSite() {
  const envBase = process.env.BASE_PATH?.replace(/\/+$/, '');
  const envOrigin = process.env.SITE_URL?.replace(/\/+$/, '');

  try {
    const index = path.join(distDir, 'sitemap-index.xml');
    const loc = existsSync(index) ? readFileSync(index, 'utf8') : '';
    const url = loc.match(/<loc>(.*?)<\/loc>/)?.[1];
    if (url) {
      const parsed = new URL(url);
      const pathname = parsed.pathname; // /sub/sitemap-0.xml → /sub
      return {
        origin: envOrigin ?? parsed.origin,
        base: envBase ?? pathname.replace(/\/[^/]*$/, '').replace(/\/+$/, ''),
      };
    }
  } catch {
    // 取不到就按根路径，交给下面的检查结果说话
  }
  return { origin: envOrigin ?? '', base: envBase ?? '' };
}

const { origin, base } = resolveSite(); // origin='' 或 'https://user.github.io'；base='' 或 '/sub'

async function walkHtml(dir) {
  const found = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) found.push(...(await walkHtml(full)));
    else if (entry.name.endsWith('.html')) found.push(full);
  }
  return found;
}

/** 把站内 URL 映射为 dist 内的文件路径；外部链接与锚点返回 null（不检查） */
function toDiskPath(url) {
  if (!url) return null;
  const clean = url.split('#')[0].split('?')[0];
  if (!clean || clean.startsWith('http') || clean.startsWith('mailto:') || clean.startsWith('//')) {
    return null;
  }

  let pathname = clean.startsWith('/') ? clean : null;
  if (pathname === null) return null; // 相对链接本仓库未使用，忽略

  if (base && pathname.startsWith(base)) pathname = pathname.slice(base.length);
  if (!pathname.startsWith('/')) pathname = `/${pathname}`;

  const target = path.join(distDir, pathname);
  return target;
}

async function exists(target) {
  if (existsSync(target)) {
    const info = await stat(target);
    if (info.isDirectory()) return existsSync(path.join(target, 'index.html'));
    return true;
  }
  return false;
}

/**
 * 托管前缀 / 站外托管检查：返回问题描述，正常返回 null。
 * 根路径托管（base 为空）时站内根绝对路径本就合法，只查 *.github.io 的宿主是否与本站一致。
 */
function findLeak(url) {
  const clean = url.split('#')[0].split('?')[0];
  if (!clean) return null;

  if (!clean.startsWith('http') && clean.startsWith('/') && !clean.startsWith('//')) {
    if (base && clean !== base && !clean.startsWith(`${base}/`)) {
      return `根绝对路径未带托管前缀——上线会请求域名根路径而非 ${base}/，必然 404`;
    }
    return null;
  }

  // origin 探测失败时无从判断站外，跳过（宁可漏报也不误报）
  const host = clean.match(/^https?:\/\/([^/]+)/i)?.[1];
  if (!host || !host.toLowerCase().endsWith('.github.io')) return null;

  let site;
  try {
    site = new URL(origin);
  } catch {
    return null;
  }

  if (host.toLowerCase() !== site.host.toLowerCase()) {
    return `指向站外托管 ${host}（本站为 ${site.host}）——改名/换托管前的旧域残留`;
  }

  const rest = clean.slice(clean.indexOf(host) + host.length);
  if (base && rest !== base && !rest.startsWith(`${base}/`)) {
    return `本站地址缺少托管前缀——上线路径为 ${rest || '/'}，应为 ${base}/，必然 404`;
  }
  return null;
}

const pages = await walkHtml(distDir);
const broken = [];
const leaks = [];
const ATTR = /(?:href|src)="([^"]+)"/g;

for (const page of pages) {
  const html = await readFile(page, 'utf8');
  const seen = new Set();

  for (const match of html.matchAll(ATTR)) {
    const url = match[1];
    if (seen.has(url)) continue;
    seen.add(url);

    const leak = findLeak(url);
    if (leak) {
      leaks.push({ page: path.relative(distDir, page), url, leak });
      continue; // 前缀都不对，再校验文件存在与否没有意义
    }

    const target = toDiskPath(url);
    if (!target) continue;
    if (!(await exists(target))) {
      broken.push(`${path.relative(distDir, page)} → ${url}`);
    }
  }
}

console.log(
  `[check-links] 扫描 ${pages.length} 个页面 · 站内链接与资源 · base="${base || '/'}" · origin="${origin || '未知'}"`,
);

if (leaks.length > 0) {
  console.error(
    `\n[check-links] FAIL 发现 ${leaks.length} 处托管前缀失配的链接（本地能找到文件，上线必 404）：\n`,
  );
  for (const item of leaks) console.error(`  ${item.page} → ${item.url}\n      ${item.leak}`);
  console.error(
    '\n  处理方式：站内链接一律经 withBase/前缀管线生成；绕开管线的产物（deck、PDF）按构建期 BASE_PATH 拼接。',
  );
  process.exit(1);
}

if (broken.length > 0) {
  console.error(`\n[check-links] FAIL 发现 ${broken.length} 个死链：\n`);
  for (const item of broken) console.error(`  ${item}`);
  process.exit(1);
}

console.log('[check-links] PASS 无死链');
