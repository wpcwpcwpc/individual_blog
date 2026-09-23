/**
 * 仓库根定位 —— 构建期组件会被打包进 dist/，此时 import.meta.url 已不再指向源码位置，
 * 任何"相对模块取文件"的路径都会失锚。这里统一改为先认运行目录（npm 脚本与构建都在仓库根执行），
 * 失配时再回退到模块相对位置。
 */
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

function findRoot() {
  const fromModule = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
  const candidates = [process.cwd(), fromModule];
  return candidates.find((dir) => existsSync(path.join(dir, 'package.json'))) ?? fromModule;
}

export const projectRoot = findRoot();

/** 拼出仓库内路径：projectPath('public', 'pdf') */
export function projectPath(...segments) {
  return path.join(projectRoot, ...segments);
}
