/**
 * PDF 导出 —— 把已构建的项目页打印为 PDF，作为离线阅读与邮件附件的兜底产物。
 *
 * 设计要点（对应 specs/site-deploy「PDF 兜底产物非阻塞」）：
 * - 产物写到 public/pdf/（供下次构建渲染下载入口）与 dist/pdf/（供本次部署直接带上）
 * - 任何一步失败都只告警并以 0 退出：PDF 是兜底产物，绝不允许拖垮站点发布
 * - 用本机 Chromium 打印（真实浏览器排版），不引入 puppeteer / playwright 这类重依赖
 *
 * 必需环境：本机存在 Chrome 或 Edge；否则跳过。
 */
import { readdir, readFile, mkdir, copyFile, rm } from 'node:fs/promises';
import { existsSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const distDir = path.join(root, 'dist');
const outDirs = [path.join(root, 'public', 'pdf'), path.join(root, 'dist', 'pdf')];
const tmpDir = path.join(root, '.pdf-tmp');

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
].filter(Boolean);

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.xml': 'application/xml',
  '.txt': 'text/plain; charset=utf-8',
  '.pdf': 'application/pdf',
  '.woff2': 'font/woff2',
};

function findBrowser() {
  return CHROME_CANDIDATES.find((candidate) => existsSync(candidate));
}

/** 起一个只读静态服务，把 dist/ 暴露出来（Chromium 打印需要 http 协议才能正确加载资源） */
async function serveDist() {
  const server = createServer(async (req, res) => {
    try {
      const urlPath = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
      let filePath = path.join(distDir, urlPath);
      if (existsSync(filePath) && statSync(filePath).isDirectory()) {
        filePath = path.join(filePath, 'index.html');
      }
      const body = await readFile(filePath);
      res.writeHead(200, { 'Content-Type': MIME[path.extname(filePath)] ?? 'application/octet-stream' });
      res.end(body);
    } catch {
      res.writeHead(404).end('not found');
    }
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, port: server.address().port };
}

function printToPdf(browser, url, output) {
  return new Promise((resolve) => {
    const child = spawn(
      browser,
      [
        '--headless=new',
        '--disable-gpu',
        '--hide-scrollbars',
        '--no-pdf-header-footer',
        `--print-to-pdf=${output}`,
        url,
      ],
      { stdio: 'ignore' },
    );
    child.on('exit', (code) => resolve(code === 0));
    child.on('error', () => resolve(false));
  });
}

const browser = findBrowser();
if (!browser) {
  console.warn('[pdf] 未找到 Chrome/Edge，跳过 PDF 导出（不影响站点发布）');
  process.exit(0);
}

if (!existsSync(distDir)) {
  console.warn('[pdf] dist/ 不存在，请先执行构建。跳过 PDF 导出');
  process.exit(0);
}

const slugs = (await readdir(path.join(distDir, 'projects'), { withFileTypes: true }).catch(() => []))
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name);

if (slugs.length === 0) {
  console.log('[pdf] 没有项目页需要导出');
  process.exit(0);
}

await rm(tmpDir, { recursive: true, force: true });
await mkdir(tmpDir, { recursive: true });
for (const dir of outDirs) await mkdir(dir, { recursive: true });

const { server, port } = await serveDist();
let ok = 0;

try {
  for (const slug of slugs) {
    const tmpFile = path.join(tmpDir, `${slug}.pdf`);
    const printed = await printToPdf(browser, `http://127.0.0.1:${port}/projects/${slug}/`, tmpFile);

    if (!printed || !existsSync(tmpFile)) {
      console.warn(`[pdf] ${slug} 导出失败，跳过（不影响站点发布）`);
      continue;
    }

    for (const dir of outDirs) {
      await copyFile(tmpFile, path.join(dir, `${slug}.pdf`));
    }
    ok += 1;
    console.log(`[pdf] ${slug} → public/pdf/${slug}.pdf`);
  }
} finally {
  server.close();
  await rm(tmpDir, { recursive: true, force: true });
}

console.log(`[pdf] 完成 ${ok}/${slugs.length}`);
