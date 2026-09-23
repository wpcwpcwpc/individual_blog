/**
 * 站点级元数据 —— 名称、定位、联系方式、导航的唯一真源。
 *
 * 身份与联系方式不在此处硬编码，一律从 `data/profile.ts`（履历真源）取，
 * 避免首页、关于页、简历页各写一份"我是谁"。
 */
// 扩展名必须写全：构建脚本（如 deck）用纯 Node 直接 import 本文件，
// 而 Node 的 ESM 解析不做扩展名补全（Astro/Vite 侧两者都能解析）。
import { contact, directions, identity } from './data/profile.ts';

export const SITE = {
  name: identity.name, // 站点名：域名锚人，这里用真实姓名
  author: identity.name,
  tagline: identity.headline, // 一句话定位：与首页身份区、关于页共用同一句
  description: `个人技术站：${directions.join(' / ')}。项目实践、工程取舍与技术写作。`,
  email: contact.email, // 对外联系邮箱（简历同源）
  github: contact.github,
  nav: [
    { label: '首页', href: '/' },
    { label: '关于', href: '/about' },
    { label: '源码解析', href: '/claudecode' },
    { label: '简历', href: '/resume' },
  ],
} as const;
