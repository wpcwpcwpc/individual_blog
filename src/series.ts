/**
 * 专栏登记表 —— 专栏元数据的唯一真源。
 *
 * 专区页、文章列表页与章节页都从这里取标题、描述与分区顺序；
 * 页面内不写死分区数组，内容文件也不重复描述专栏。
 * 章节与专栏的一致性（专栏存在、分区命中、序号唯一）由 scripts/check-series.mjs 在构建前强制。
 */

export interface SeriesGroup {
  /** 分区名：文章 frontmatter 的 seriesGroup 必须逐字命中其一 */
  name: string;
  /** 分区说明，展示在专区页的分区标题下 */
  note: string;
}

export interface Series {
  /** 专栏 id：文章 frontmatter 的 series 取值 */
  id: string;
  title: string;
  description: string;
  /** 分区顺序即展示顺序 */
  groups: SeriesGroup[];
}

export const SERIES: Series[] = [
  {
    id: 'claudecode',
    title: 'Claude Code CLI 源码解析',
    description:
      '逐层拆开一个真实的 Agent CLI：查询引擎、消息与状态、工具与 MCP 集成、多 Agent 协作、安全与可观测性，以及工程化支撑。每章落到可迁移的设计原则上。',
    groups: [
      { name: '导读', note: '先建立起全局地图，再决定从哪条路径读下去' },
      { name: '核心架构', note: '一次请求如何变成一次完整的往返' },
      { name: '工具与能力扩展', note: 'Agent 的手脚从哪来，边界划在哪' },
      { name: 'Agent 核心系统', note: '从单 Agent 到协作：记忆、编排与内置 Agent' },
      { name: '安全与可观测性', note: '版本控制、沙箱、错误处理与插件边界' },
      { name: '工程支撑', note: '测试、构建、配置、规范与实战迁移' },
    ],
  },
];

/** 按 id 取专栏；未登记返回 undefined（由构建前校验拦下，不在页面里兜底） */
export function getSeries(id: string): Series | undefined {
  return SERIES.find((series) => series.id === id);
}
