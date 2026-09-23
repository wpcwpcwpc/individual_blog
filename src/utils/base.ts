/**
 * 资源路径前缀工具 —— 使站点在根路径与子路径托管下都可用。
 * 前缀由构建期环境变量 BASE_PATH 决定（见 astro.config.mjs）。
 */
const base = import.meta.env.BASE_URL;

/** 把站内绝对路径拼上前缀：withBase('/about') → '/about' 或 '/sub/about' */
export function withBase(path: string): string {
  const prefix = base.replace(/\/$/, '');
  const suffix = path.replace(/^\//, '');
  return suffix ? `${prefix}/${suffix}` : `${prefix}/`;
}
