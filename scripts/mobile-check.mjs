/**
 * 手机端核对 —— 用 CDP 强制 390×844 真视口，测横向溢出并出图。
 *
 * 为什么不用 `--window-size=390`：Windows 上 Chrome 有 ~485px 最小窗口宽度，
 * 传 390 只会被抬到 485 再裁图，看起来"溢出"其实是假象。
 *
 * 用法：
 *   npm run mobile -- /projects/qa-agent             # 扫 dist/ 下的站点页面
 *   npm run mobile -- /deck/qa-agent/ --h 2600       # 出整页截图
 */
import { createServer } from 'node:http';
import { readFile, writeFile, stat, rm } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { projectPath } from '../src/utils/paths.mjs';

const CHROME_CANDIDATES = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
];

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css',
  '.js': 'text/javascript',
  '.mjs': 'text/javascript', // pdf.js 的 worker 是 .mjs：MIME 不对会被浏览器拒绝加载
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.pdf': 'application/pdf',
  '.xml': 'application/xml',
  '.txt': 'text/plain',
};

const args = process.argv.slice(2);
const page = args.find((arg) => !arg.startsWith('--')) ?? '/';
const height = Number(args[args.indexOf('--h') + 1]) || 0;
const out = args[args.indexOf('--out') + 1] || null;
const width = Number(args[args.indexOf('--w') + 1]) || 390;
const offsetY = Number(args[args.indexOf('--y') + 1]) || 0;

const dist = projectPath('dist');
const profile = projectPath('_cdp-profile');

/** 起一个只读 dist/ 的静态服务（站点页面的资源路径是绝对路径，file:// 下会丢样式） */
const server = createServer(async (req, res) => {
  let file = path.join(dist, decodeURIComponent(req.url.split('?')[0]));
  try {
    if ((await stat(file)).isDirectory()) file = path.join(file, 'index.html');
  } catch {
    file = path.join(dist, '404.html');
  }
  try {
    const body = await readFile(file);
    res.writeHead(200, { 'content-type': TYPES[path.extname(file)] ?? 'application/octet-stream' });
    res.end(body);
  } catch {
    res.writeHead(404).end('not found');
  }
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;

const chrome = spawn(CHROME_CANDIDATES[0], [
  '--headless=new',
  '--disable-gpu',
  '--hide-scrollbars',
  '--remote-debugging-port=0',
  `--user-data-dir=${profile}`,
  'about:blank',
]);

const wsUrl = await new Promise((resolve, reject) => {
  let buffer = '';
  const timer = setTimeout(() => reject(new Error('DevTools 端口超时')), 20000);
  chrome.stderr.on('data', (chunk) => {
    buffer += chunk.toString();
    const match = buffer.match(/ws:\/\/[^\s]+/);
    if (match) {
      clearTimeout(timer);
      resolve(match[0]);
    }
  });
});

const socket = new WebSocket(wsUrl);
await new Promise((resolve) => socket.addEventListener('open', resolve));
let nextId = 0;
const pending = new Map();
socket.addEventListener('message', (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    pending.get(msg.id)(msg.result);
    pending.delete(msg.id);
  }
});
const send = (method, params = {}, sessionId) =>
  new Promise((resolve) => {
    const id = (nextId += 1);
    pending.set(id, resolve);
    socket.send(JSON.stringify({ id, method, params, sessionId }));
  });

const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
await send('Page.enable', {}, sessionId);
await send(
  'Emulation.setDeviceMetricsOverride',
  { width, height: 844, deviceScaleFactor: 2, mobile: width < 600 },
  sessionId,
);
await send('Page.navigate', { url: origin + page }, sessionId);
await new Promise((resolve) => setTimeout(resolve, 2500));

const { result } = await send(
  'Runtime.evaluate',
  {
    expression: `JSON.stringify({
      innerWidth: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      offenders: [...document.querySelectorAll('body *')]
        .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1)
        /* 自带宽滚动容器的内容（代码块、表格、图表）属正常：它自己滚，不撑页面 */
        .filter((el) => {
          for (let node = el; node; node = node.parentElement) {
            const overflowX = getComputedStyle(node).overflowX;
            if (overflowX === 'auto' || overflowX === 'scroll' || overflowX === 'hidden') return false;
          }
          return true;
        })
        .map((el) => el.tagName + (el.className && typeof el.className === 'string' ? '.' + el.className.slice(0, 30) : ''))
        .slice(0, 8),
      title: document.title.slice(0, 50),
    })`,
    returnByValue: true,
  },
  sessionId,
);
console.log(`[mobile] ${page} ${result.value}`);

if (height > 0) {
  const target = out ?? projectPath('_mobile.png');
  const shot = await send(
    'Page.captureScreenshot',
    { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: offsetY, width, height, scale: 1 } },
    sessionId,
  );
  await writeFile(target, Buffer.from(shot.data, 'base64'));
  console.log(`[mobile] 截图 → ${path.basename(target)}`);
}

socket.close();
chrome.kill();
server.close();
// Chrome 退出时会短暂占用 profile 目录，删不掉不影响结果（该目录已在 .gitignore 里）
await new Promise((resolve) => setTimeout(resolve, 400));
await rm(profile, { recursive: true, force: true }).catch(() => {});
process.exit(0);
