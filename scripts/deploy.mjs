/**
 * 部署 —— 把 dist/ 发布到 Cloudflare Pages。
 *
 * 凭据全部来自环境变量，绝不写入仓库：
 *   CLOUDFLARE_API_TOKEN   具有 Pages 编辑权限的 API Token
 *   CLOUDFLARE_ACCOUNT_ID  Cloudflare 账户 ID
 *   CF_PROJECT_NAME        Pages 项目名（缺省 personal-site）
 *   SITE_URL / BASE_PATH   构建期已由 astro.config.mjs 读取
 *
 * 实际上传交给 wrangler（官方工具），本脚本只负责校验与转发，
 * 避免自己实现上传协议而随平台变化失效。
 *
 * 用法：
 *   npm run deploy                  # 构建 + 导出 PDF + 部署
 *   仅部署（已有 dist）：node scripts/deploy.mjs
 */
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const distDir = path.join(root, 'dist');

const token = process.env.CLOUDFLARE_API_TOKEN;
const accountId = process.env.CLOUDFLARE_ACCOUNT_ID;
const projectName = process.env.CF_PROJECT_NAME ?? 'personal-site';

const missing = [];
if (!token) missing.push('CLOUDFLARE_API_TOKEN');
if (!accountId) missing.push('CLOUDFLARE_ACCOUNT_ID');

if (missing.length > 0) {
  console.error(
    `[deploy] 缺少环境变量：${missing.join('、')}\n` +
      '  请在本地或 CI 中设置后重试。凭据不得提交进仓库。',
  );
  process.exit(1);
}

if (!existsSync(distDir)) {
  console.error('[deploy] dist/ 不存在。请先执行 npm run build 构建站点。');
  process.exit(1);
}

console.log(`[deploy] 部署 dist/ → Cloudflare Pages 项目「${projectName}」`);

const child = spawn(
  'npx',
  ['--yes', 'wrangler@latest', 'pages', 'deploy', distDir, `--project-name=${projectName}`, '--branch=main'],
  {
    stdio: 'inherit',
    env: {
      ...process.env,
      CLOUDFLARE_API_TOKEN: token,
      CLOUDFLARE_ACCOUNT_ID: accountId,
    },
    shell: process.platform === 'win32',
  },
);

child.on('exit', (code) => {
  if (code === 0) {
    console.log('[deploy] 部署完成');
  } else {
    console.error(`[deploy] 部署失败（退出码 ${code}）`);
  }
  process.exit(code ?? 1);
});
