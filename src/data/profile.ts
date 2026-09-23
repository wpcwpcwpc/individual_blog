/**
 * 履历与能力数据 —— 首页身份区、技术栈区块、简历文本页的共同真源。
 *
 * 为什么是结构化数据而不是 Markdown：同一件事在首页与简历页会被抄两遍，抄写即漂移。
 * 这里定义"事实有哪些"，页面只决定"展示哪一部分"（首页取 identity / directions / stacks，
 * 简历文本页取全量）。改一处，两处同步生效。
 *
 * 与简历 PDF 的关系：PDF 由人工产出，是投递与打印形态；本文件是站点阅读形态。
 * 两者一致性由 scripts/check-resume-sync.mjs 在构建前校验（站点侧事实必须都能在 PDF 里找到）。
 *
 * 口径约束（见 GLOSSARY.md 第三节）：精确履历数值只允许出现在简历文本页与 PDF；
 * 站内展示区（首页、项目页、文章）一律用区间表述。
 */

/** 身份信息：首页身份区与简历文本页页首共用 */
export const identity = {
  name: '文棚嶒',
  /** 一句话定位：招聘方扫读时唯一必须看进去的一句 */
  headline: '做 Agent 工程与质量效能：把多 Agent 系统从"能跑"推到"可评测、可观测、可交付"。',
  location: '杭州',
  /** 年限用区间表述，避免写成时间点后逐年老化 */
  experience: '三年以上研发经验',
  /** 自我介绍：三句以内，写清方向、来路与当前战场 */
  intro: [
    '主战场是 Agent 系统的工程化：编排与调度、工具与协议、评测与可观测，把模型能力落成能上生产的工程件。',
    '来路是测试开发与质量效能平台，习惯先想"怎么验证"再想"怎么写"——这套视角在 AI 应用上同样是稀缺品。',
    '当前在做游戏 QA 场景的 Agent 平台，覆盖环境管理、版本操作、客户端调试与测试执行。',
  ],
} as const;

/** 方向标签：首页展示，四个以内，超出就合并 */
export const directions = [
  'Agent 开发',
  'Agent 评测与可观测',
  'AI 应用工程',
  '测试开发 / 质量效能平台',
] as const;

/**
 * 技术栈：按能力分组。
 * 硬约束：分组 ≤ 5、每组条目 ≤ 6（见 hire-profile 规格）——条目只写技术名或能力名，不写解释句。
 */
export const stacks = [
  {
    name: 'Agent / LLM 工程',
    items: ['Agno', 'LangChain', 'ReAct', 'RAG', '多 Agent 协作（MAS）', 'MCP 与 Tool Call'],
  },
  {
    name: '评测与可观测',
    items: ['评测集建设', 'LLM-as-Judge 评分', 'Scorecard 与回归基线', 'LLM Tracing', 'Token 与上下文用量观测'],
  },
  {
    name: '后端与服务',
    items: ['Python', 'FastAPI', 'Django', 'Redis', 'SSE / WebSocket 流式'],
  },
  {
    name: '数据与客户端',
    items: ['MySQL', 'MongoDB', 'React', 'PyQt', 'PyInstaller / Inno Setup 打包'],
  },
  {
    name: '工程方法',
    items: ['SDD / TDD', 'Spec Change 驱动开发', 'Git / SVN', '接口与自动化测试', '持续交付'],
  },
] as const;

export interface Experience {
  company: string;
  title: string;
  /** 时间区间：起始年月、结束年月或"至今" */
  period: string;
  location: string;
  highlights: string[];
}

export const experience: Experience[] = [
  {
    company: '网易（杭州）网络有限公司',
    title: '高级测试开发工程师',
    period: '2025.12 – 至今',
    location: '杭州',
    highlights: [
      '主导 QA Agent 平台与配套质量工作台研发：桌面端承载工具入口与本地调试，Web 管理端承载 Agent 会话、任务、评测与团队协作，服务侧承载编排与工具执行。',
      '基于通用 Agent 编排框架建成 19 个职责 Agent（规划、环境、执行、后处理、用例生成与修订等），设计 Coordinator + WorkerPool 多 Agent 团队调度模式，并按 Reason / Plan / ToolCall 场景做模型路由，平衡效果与成本。',
      '设计 30+ MCP 与内置工具，把客户端启停、服务端管理、版本分支切换、游戏状态查询、指令通道与远程设备执行封装为标准 Tool，打通 Agent、游戏进程与本地环境。',
      '建立 Agent 工程化底座：三层 Hook（上下文注入、工具越权拦截、Prompt 治理）、Memory 蒸馏与向量检索、会话崩溃恢复、沙箱工作区与人机协同。',
      '建成 Agent 评测体系：自动 Judge 评分、Scorecard、版本覆盖回归与 bug 覆盖评测，用例生成质量从人工抽检转为自动全量评测，单轮回归评测由小时级降至分钟级。',
      '落地全链路可观测：接入 LLM Tracing 并自研用量看板，Token 消耗与上下文占用全程可视，长链路异常定位由 30min+ 降至面板分钟级。',
      '平台业务自动化覆盖率超 80%，数字员工日均处理 50+ 操作请求，日均节省约 2 pd。',
    ],
  },
  {
    company: '联想（北京）有限公司',
    title: 'CSP Product Assurance Engineer',
    period: '2023.08 – 2025.12',
    location: '北京',
    highlights: [
      '负责测试执行平台研发，搭建分布式并发测试架构，基于 Redis 与 Celery 实现测试任务编排、状态追踪与全生命周期监控，每月节省约 80 人日。',
      '独立研发 AI 日志分析系统：用 ReAct 与父子图工作流处理长文本日志，FastAPI 支持异步并发，PostgreSQL 持久化分析记忆，提升复盘效率与结论一致性。',
      '推动 AI 能力融入质量工程流程，围绕日志归因、测试复盘与问题定位设计可扩展的 Agent 化方案。',
    ],
  },
];

export interface Education {
  school: string;
  degree: string;
  period: string;
  notes: string[];
}

export const education: Education[] = [
  {
    school: '北京工业大学',
    degree: '软件工程 · 硕士（全日制）',
    period: '2020.09 – 2023.07',
    notes: ['AI 领域发明专利 2 项', 'SCI Q2 期刊论文 1 篇', 'C 类会议论文 3 篇'],
  },
  {
    school: '重庆交通大学',
    degree: '通信工程 · 本科（全日制）',
    period: '2016.09 – 2020.06',
    notes: ['ACM 重庆赛区二等奖', '蓝桥杯省赛二等奖'],
  },
];

/**
 * 对外联系方式 —— 站点与简历共用，唯一真源。
 * 只放可公开渠道：手机号不上站（见 GLOSSARY.md 第〇节与 showcase-resume-handoff 规格）。
 */
export const contact = {
  email: '786939553@qq.com',
  github: 'https://github.com/wpcwpcpc',
} as const;
