## 1. 履历单一真源

- [x] 1.1 新建 `src/data/profile.ts`，导出 `identity`（姓名、一句话定位、简介、所在地）、`directions`（方向标签）、`stacks`（分组技术栈，5 组以内、每组 6 条以内）、`experience`（公司、职位、时间区间、要点数组）、`education`、`contact`（邮箱、代码托管主页；不含手机号）
- [x] 1.2 技术栈分组按简历口径落数据：Agent/LLM 工程、Agent 评测与可观测、后端与服务、数据与客户端、工程方法
- [x] 1.3 简介文案定稿：3 句以内，写清"做 Agent 工程与质量效能方向、做过几年、在杭州"，MUST NOT 出现"正在找工作"等时效性表述
- [x] 1.4 `src/site.config.ts` 的 `tagline` 与 `description` 按新定位改写，并在其中引用 `profile` 的方向或截断简介（避免两处各写一份）

## 2. 脱敏口径切换（实名雇主）

- [x] 2.1 本地 `GLOSSARY.local.md` 的禁出词表移除雇主名（网易、联想）及其关联写法，跑 `npm run sanitize` 确认不再命中
- [ ] 2.2 同步更新 CI secret `GLOSSARY_WORDS`（去掉同一批词）；本地删了 CI 没删会导致 CI 假拦截 —— **需你在 GitHub 仓库设置里操作**
- [x] 2.3 `GLOSSARY.md` 第〇节与第四节更新：写明雇主公开名称已确认可实名、站内实名口径与 PDF 一致
- [x] 2.4 `GLOSSARY.md` 第三节登记项扩展：新增"履历原文精确值（简历文本页与 PDF 通用）"的适用说明，明确"站内展示用区间、简历用精确值"为两个合法口径

## 3. 首页身份区与技术栈

- [x] 3.1 改写 `src/pages/index.astro` 的 hero：姓名 + 一句话定位 + 简介段落 + 方向标签（≤4 个）+ 入口（简历 / 项目 / 邮箱）
- [x] 3.2 首页 hero 之后新增「技术栈」区块，从 `profile.stacks` 渲染为紧凑的「组名 + 条目」排版，不用卡片阵列
- [x] 3.3 首页末尾新增联系区，仅邮箱与代码托管主页，取值来自站点配置
- [x] 3.4 区块顺序确认为：身份区 → 技术栈 → 精选项目 → 其他项目 → 文章 → 联系；空内容集合走占位提示，不整段消失
- [x] 3.5 在 `src/styles/typography.css` 落地身份区与技术栈区块的排版规则，不把样式散落在页面 `<style>` 里
- [x] 3.6 `src/pages/about.astro` 简介段改为引用 `profile.identity` 的简介，避免与首页重复维护

## 4. 简历双形态

- [x] 4.1 `/resume` 改为文本简历页：从 `profile` 渲染姓名、方向、技术栈、经历、教育、联系方式，用语义化标签（h2 分节 + ul 列条目），提供 PDF 下载入口
- [x] 4.2 现有 pdf.js 渲染实现整体搬到 `/resume/pdf`，代码逻辑不变（懒渲染、缩放重画、失败回退内置阅读器、`<noscript>` 兜底）
- [x] 4.3 两个页面互链：文本页给「查看/下载 PDF 原件」，PDF 页给「返回文本版」
- [x] 4.4 PDF 缺失时文本页完整可用，PDF 入口显示不可用提示
- [x] 4.5 更新 `src/site.config.ts` 导航（如需要）与既有指向 `/resume` 的文案，确认语义仍准确

## 5. 校验脚本与构建链

- [x] 5.1 新建 `scripts/check-resume-sync.mjs`：用 `pdfjs-dist` 提取 `public/resume/resume.pdf` 文本，断言站点侧关键事实（雇主名、学校、学位、时间区间、指标精确值）均出现，缺失则非零退出并列出缺失条目
- [x] 5.2 校验方向限定为单向包含（PDF ⊇ 站点侧事实），PDF 多出的内容不报错
- [x] 5.3 把新脚本接入 `package.json` 的 `build` 链（与 `sanitize`、`figures`、`pdf-audit` 并列，任一失败即中止），并在 CI workflow 加同名闸门步骤
- [x] 5.4 跑一次 `npm run build` 全绿 —— 已全绿（sanitize / figures / series / pdf-audit / resume-sync / diagrams / deck / og / astro build 38 页 / check 无死链）。为达成这一点，额外把 `openspec/changes/add-qa-agent-source/tasks.md`（源码搬运台账，按其自身规格 MUST NOT 入库）列入 `.gitignore` 与扫描器 `IGNORE`；并修正 `site.config.ts` 的 import 扩展名（纯 Node 解析不做补全，deck 构建会挂）

## 6. 文档与发布

- [x] 6.1 改写 `RESUME.md`：删除"PDF 是唯一内容源"条款，改为双形态分工（PDF 投递/打印、站点阅读），并记录改简历需同时更新 `profile.ts` 与 PDF 且以构建校验为准
- [x] 6.2 `README.md` 补一段站点定位说明（求职门面 + 技术写作），并补 `resume-sync` 命令、`data/profile.ts` 目录项、`/resume` 与 `/resume/pdf` 的 URL 契约
- [x] 6.3 人工终审：按"雇主与外部读者都能看到"标准通读首页、`/resume`，确认无时效性文案、无手机号、无未登记精确数字
- [x] 6.4 移动端过一遍（`npm run mobile`）：首页与 `/resume` 在 390px 下 `offenders: []`、无横向溢出，身份区与技术栈可在一屏内扫读
- [ ] 6.5 `npm run check` 通过（已 PASS，39 页无死链）；`npm run deploy` 待你确认后再跑（会推送到托管，属对外可见动作）

## 7. 待确认细节（实施时定）

- [x] 7.1 简介里是否写工作年限：写，用区间表述"三年以上研发经验"，避免时间点老化
- [x] 7.2 首页联系区邮箱用明文展示（方便招聘方复制），并同时在页脚保留
- [ ] 7.3 PDF 里技术栈一行有多余顿号（`MySQL、MongoDB、；`），换 PDF 时顺手修掉 —— 需你出新版 `resume.pdf`
