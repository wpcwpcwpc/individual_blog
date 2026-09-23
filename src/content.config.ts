import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

// 关键数字登记项：口径的单一来源是 GLOSSARY.md 第三节，
// 此处只保存"页面上要展示什么"；两者的取值由 scripts/check-figures.mjs 在构建前强制一致。
const figure = z.object({
  /** 对应 GLOSSARY.md 第三节「指标区间化登记」的指标项（作为对照键） */
  register: z.string(),
  /** 页面上的短标签 */
  label: z.string(),
  /** 展示值，必须与登记的「对外表述」逐字一致 */
  value: z.string(),
});

// 项目集合：/projects/<id> 为对外引用路径（简历指向此处），id 由文件名决定，一经发布不得变更。
const projects = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/projects' }),
  schema: z.object({
    title: z.string(),
    tagline: z.string().max(80),
    summary: z.string(),
    order: z.number().default(100),
    featured: z.boolean().default(false),
    tags: z.array(z.string()).default([]),
    draft: z.boolean().default(false),
    updated: z.coerce.date().optional(),
    /** 在线演示地址：站外绝对 URL，不走 withBase 前缀管线 */
    demoUrl: z.string().url().optional(),
    figures: z.array(figure).default([]),
  }),
});

// 文章集合：/posts/<id> 为文章路径；属于专栏的文章额外承载章节元数据，详情页改走 /claudecode/<id>。
const posts = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/posts' }),
  schema: z.object({
    title: z.string(),
    summary: z.string(),
    publishedAt: z.coerce.date(),
    tags: z.array(z.string()).default([]),
    draft: z.boolean().default(false),
    updated: z.coerce.date().optional(),
    /** 专栏 id：取值必须在 src/series.ts 登记（由 scripts/check-series.mjs 强制） */
    series: z.string().optional(),
    /** 专栏内章节序号：决定专区页顺序，同一专栏内唯一 */
    seriesOrder: z.number().optional(),
    /** 专栏内的分区名：必须命中专栏登记表的分区（由 scripts/check-series.mjs 强制） */
    seriesGroup: z.string().optional(),
    /** 阅读时长（分钟），供专区页展示 */
    readingMinutes: z.number().optional(),
    /** 重要程度（1–3 星），供专区页展示 */
    weight: z.number().optional(),
  }),
});

export const collections = { projects, posts };
