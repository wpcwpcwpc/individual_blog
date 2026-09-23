import { getCollection, type CollectionEntry } from 'astro:content';
import { getSeries, type Series } from '../series';

/**
 * 内容查询统一入口 —— 草稿过滤、排序与"专栏 / 单篇"的划分只在此处定义，
 * 首页、列表页与专区页共用，避免规则散落导致"列表可见、详情 404"之类的不一致。
 */

export type ProjectEntry = CollectionEntry<'projects'>;
export type PostEntry = CollectionEntry<'posts'>;

/** 已发布项目，按 order 升序（数字小者靠前，同值按标题） */
export async function listProjects(): Promise<ProjectEntry[]> {
  const projects = await getCollection('projects', ({ data }) => !data.draft);
  return projects.sort(
    (a, b) => a.data.order - b.data.order || a.data.title.localeCompare(b.data.title),
  );
}

/** 已发布文章（含专栏章节），按发布日期倒序 */
export async function listPosts(): Promise<PostEntry[]> {
  const posts = await getCollection('posts', ({ data }) => !data.draft);
  return posts.sort((a, b) => b.data.publishedAt.valueOf() - a.data.publishedAt.valueOf());
}

/** 是否属于某个专栏（列表页与首页的单篇区都要把它们排除掉） */
export function isSeriesPost(post: PostEntry): boolean {
  return Boolean(post.data.series);
}

/** 单篇文章：不属于任何专栏的独立文章，按发布日期倒序 */
export async function listStandalonePosts(): Promise<PostEntry[]> {
  return (await listPosts()).filter((post) => !isSeriesPost(post));
}

export interface SeriesChapter {
  post: PostEntry;
  order: number;
}

export interface SeriesGroupChapters {
  name: string;
  note: string;
  chapters: SeriesChapter[];
}

export interface SeriesBundle {
  series: Series;
  groups: SeriesGroupChapters[];
  chapters: SeriesChapter[];
}

/** 取某个专栏的章节，按登记表的分区顺序分组；空分区不出现在结果里 */
export async function getSeriesBundle(id: string): Promise<SeriesBundle | undefined> {
  const series = getSeries(id);
  if (!series) return undefined;

  const chapters = (await listPosts())
    .filter((post) => post.data.series === id && typeof post.data.seriesOrder === 'number')
    .map((post) => ({ post, order: post.data.seriesOrder as number }));

  const groups = series.groups
    .map((group) => ({
      name: group.name,
      note: group.note,
      chapters: chapters
        .filter(({ post }) => post.data.seriesGroup === group.name)
        .sort((a, b) => a.order - b.order),
    }))
    .filter((group) => group.chapters.length > 0);

  return { series, groups, chapters: chapters.sort((a, b) => a.order - b.order) };
}

/** 章节的上一篇 / 下一篇（同专栏内按序） */
export function neighborsOf(bundle: SeriesBundle, id: string): { prev?: PostEntry; next?: PostEntry } {
  const index = bundle.chapters.findIndex(({ post }) => post.id === id);
  if (index < 0) return {};
  return {
    prev: bundle.chapters[index - 1]?.post,
    next: bundle.chapters[index + 1]?.post,
  };
}

/** 站点内全部专栏（供首页与列表页展示入口卡片） */
export async function listSeriesBundles(): Promise<SeriesBundle[]> {
  const ids = new Set((await listPosts()).map((post) => post.data.series).filter(Boolean));
  const bundles: SeriesBundle[] = [];
  for (const id of ids) {
    const bundle = await getSeriesBundle(id as string);
    if (bundle) bundles.push(bundle);
  }
  return bundles;
}
