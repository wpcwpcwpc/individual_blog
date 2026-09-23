## 1. 内容模型与专栏登记

- [x] 1.1 `src/content.config.ts`：posts schema 增专栏字段（`series` / `seriesOrder` / `seriesGroup` / `readingMinutes` / `weight`），既有字段与其它集合不变
- [x] 1.2 新增 `src/series.ts`：登记 claudecode 专栏的 id、标题、描述与分区顺序（导读 / 核心架构 / 工具与能力扩展 / Agent 核心系统 / 安全与可观测性 / 工程支撑）
- [x] 1.3 `src/utils/content.ts` 增查询：按专栏取章节（序号升序）、按专栏分组、单篇列表；草稿过滤沿用同一处
- [x] 1.4 新增 `scripts/check-series.mjs`：章节声明了未登记专栏 → 失败；同专栏序号重复 → 失败；编号缺失 → 报出清单
- [x] 1.5 `npm run build` 接入 `check-series`（与 sanitize / figures / pdf-audit 并列），失败即中止构建

## 2. 导入脚本

- [x] 2.1 `scripts/import-series.mjs` 骨架：`--src <dir>` 传入源目录路径（**路径不写入仓库**）、源目录只读、号段白名单 00–24、黑名单 25–28 默认命中即 `exit 1`（同目录共存时需显式 `--skip-internal`）、未知号段即中止
- [x] 2.2 slug 映射表（章节序号 → `claudecode-<NN>-<english>`）；未映射号段报错退出，不允许自动生成
- [x] 2.3 内网标识改写规则表：内网 IP、`localhost:<port>`、本地磁盘绝对路径、内部域名、疑似工号长数字 → 对外泛称（规则同时收窄到"真内部资产"，避免误改公开占位内容）
- [x] 2.4 章节互链改写：`./NN-xxx.md` → `/claudecode/<NN>-<slug>`、页内锚点保留、未解析引用降级为纯文本并打印清单（代码块内不改写，避免毁掉命令行示例）
- [x] 2.5 frontmatter 生成：title / summary / publishedAt / tags / 专栏字段 / `source`（仅文件名）/ `sourceSha256`（截断摘要），正文去掉首个 H1
- [x] 2.6 `package.json` 增 `import:series` 入口
- [x] 2.7 执行导入，23 篇产物落到 `src/content/posts/claudecode-*`
- [x] 2.8 幂等验证：连续跑两次，第二次无 diff、且不触碰其它文章文件

## 3. 专区页与章节页

- [x] 3.1 `src/pages/claudecode/index.astro`：专栏标题、描述、篇数统计 + 按分区列出章节（序号 / 标题 / 阅读时长 / 重要程度）
- [x] 3.2 `src/pages/claudecode/[slug].astro`：章节正文 + 元数据行（专栏 / 分区 / 序号 / 时长 / 星级）+ 同专栏上/下篇导航 + 返回专区入口
- [x] 3.3 `src/site.config.ts` 导航增「源码解析」入口（指向 `/claudecode`）
- [x] 3.4 长文排版核对：代码块横向滚动、宽表格、引用块与目录锚点，沿用 `src/styles/typography.css` 既有体系
  - 长文暴露两处排版缺口并已修：行内 `code`/`a` 长串不断行会把窄屏撑宽（`overflow-wrap`）；正文里混入的 ASCII 图兜底断行

## 4. 文章模块重排

- [x] 4.1 `src/pages/index.astro` 文章区：专栏入口卡片 + 最近 5 篇单篇（复用 utils 查询，不再平铺全部）
- [x] 4.2 新增 `/posts` 列表页：专栏分区（卡片）+ 单篇分区（列表，每页 10 篇，含页码导航）
- [x] 4.3 确认专栏章节不出现在单篇列表，且章节详情与既有文章排版一致（章节只从 `/claudecode/<slug>` 出页，`/posts/<id>` 不再生成）

## 5. 清理示例内容

- [x] 5.1 删除 `src/content/projects/sample-project.md`
- [x] 5.2 删除 `src/content/posts/sample-post.md`
- [x] 5.3 全仓检索残留引用（首页、关于页、导航、文章互链），确认无指向示例内容的链接（`rest.length === 0` 时首页「其他项目」区自动不渲染）

## 6. 闸门与清单同步

- [x] 6.1 反证 `sanitize` 对新内容的有效拦截（临时植入一个内部产品名与一个私有网段 IP → 构建失败，随后回滚）
- [x] 6.2 `GLOSSARY.md`：按导入材料的实际命中情况增补禁出词或其泛称
  - 实际改动是**收窄**而非新增：私有网段 IP、`.internal` 占位域名、`git@github.com`、`git@host`、`C:\Temp`、`./data/x.db`、`#525659` 等公开写法原先会被误杀；收窄后用 10 例应命中 / 11 例应放过的对照集验证
- [x] 6.3 `RELEASE-CHECKLIST.md` 增两项：本次导入的抽查记录、拆解类材料可公开性的作者确认项（新增「专栏与外部导入」一节）
- [x] 6.4 站点维护说明：新增专栏的维护路径（导入命令、slug 契约、加章节步骤）→ `SERIES.md`，并在 README 的 URL 契约与命令表同步
- [x] 6.5 修内容级站内链接的托管前缀：新增 `src/plugins/rehype-base-links.mjs`（内容写根路径，构建期按 `BASE_PATH` 补前缀）。原实现下子路径托管时全站互链会 404——Astro 只给用 `withBase` 写的链接补前缀，内容里的裸路径它管不着；同时给导入脚本加"残留相对引用"自检（相对链接不进链接检查，会静默变死链）

## 7. 验证与发布

- [x] 7.1 `npm run build` 全绿（脱敏 / 口径 / 专栏 / PDF 审计 / 图表 / deck / OG / 静态构建）
- [x] 7.2 `npm run check` 无死链（37 个页面，含章节互链与 `/posts` 页码）
- [x] 7.3 390px 视口核对 `/claudecode`、`/claudecode/<slug>`、`/posts` 三处（另抽 12 章等长文核对；`mobile-check` 的溢出判定已修，不再把代码块/表格的内部滚动当页面溢出）
- [x] 7.4 抽查 3 篇章节（12 / 13 / 08）：内网标识无残留、互链可达、元数据（序号/分区/时长/星级）正确；全文人工通读仍建议作者本人做一次（见 `RELEASE-CHECKLIST.md` 第六节）
- [x] 7.5 push 触发 CI 部署，线上抽查首页、专区页、一个章节页
  - CI #8（专栏上线）与 #9（链接前缀修复）均 success；线上核对 `/individual_blog/claudecode/`（23 章、六分区、时长星级齐全）与 `/individual_blog/claudecode/00-reading-guide/`（正文、元数据行、上/下篇导航）
