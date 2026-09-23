## Why

站点目前只有 6 篇单点技术文章，缺成体系的深度内容；而手上已有一套自撰的 Claude Code CLI 源码拆解（23 篇、约 2.4 万行）——能读进别人的系统、再把实现讲成可迁移的设计原则，正是技术门面最缺的一环。同时首页把文章平铺展示，一旦引入专栏，原有 6 篇会被直接淹没：文章模块的分区与分页必须先补齐。

## What Changes

- **专栏体系（新增）**：文章集合引入 `series` 系列元数据（专栏 id / 章节序号 / 分区 / 阅读时长 / 重要程度），另建 `src/series.ts` 专栏登记表作为专区唯一真源
- **Claude Code 源码解析专区（新增）**：`/claudecode` 专区页（按「核心架构 / 工具与能力扩展 / Agent 核心系统 / 安全与可观测性 / 工程支撑」五个分区、00→24 章节顺序展示）与章节详情页 `/claudecode/<slug>`
- **导入管线（新增）**：`scripts/import-series.mjs` 从外部源目录一次性导入，**只收 00–24**（25–28 为公司内部内容，硬排除），导入期内联白名单校验、内网标识改写、章节互链改写
- **文章模块重排（修改）**：首页「文章」区改为专栏卡片 + 最近若干单篇；新增 `/posts` 文章页（分区 + 分页），文章列表不再平铺
- **清理脚手架示例内容（移除）**：删除 `src/content/projects/sample-project.md`、`src/content/posts/sample-post.md`
- **不改动**：`/projects/<slug>` 路径契约、既有 6 篇文章正文、简历页形态与 deck 产物

## Capabilities

### New Capabilities

- `claudecode-series`: 专区页与章节体系——专栏登记表、章节顺序与分区、章节页元数据与导航
- `series-import`: 外部长文导入管线——源目录边界（白名单/黑名单）、内网标识改写、章节互链改写、可重跑
- `articles-index`: 文章分区与分页——首页文章区、`/posts` 列表页、专栏与单篇的分区规则

### Modified Capabilities

- `sanitization-gate`: 新增「外部源导入边界」要求——导入脚本 MUST 显式声明来源白名单，MUST NOT 收内部内容（25–28），导入产物 MUST 通过既有禁出词闸门

## Impact

- 内容 schema：`src/content.config.ts`（posts 增 `series` 相关字段）
- 内容查询：`src/utils/content.ts`（新增按专栏/分区/章节序查询）
- 新文件：`src/series.ts`、`src/pages/claudecode/index.astro`、`src/pages/claudecode/[slug].astro`、`src/pages/posts/[...page].astro`、`scripts/import-series.mjs`
- 改动页面：`src/pages/index.astro`（文章区）、既有 `src/pages/posts/[slug].astro`（分区元数据行）
- 删除：`src/content/projects/sample-project.md`、`src/content/posts/sample-post.md`
- 脚本与清单：`package.json`（import 入口）、`GLOSSARY.md`（如新增禁出词）、`RELEASE-CHECKLIST.md`
- 产物：新增约 23 个文章页 + 1 个专区页；站点规模从 14 页增至约 40 页
- 来源边界：源文档位于公司侧目录，属**一次性导入**；仓库只保留导入产物与脚本规则，不放回源路径引用
