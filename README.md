# personal-site

个人技术门面站点，主用途是**求职门面**：进站先看到"这人是谁、会什么、在哪个方向、怎么联系"，
其后才是项目页与技术文章（它们是支撑证据，不是主角）。静态构建、零后端、零追踪。

- 技术栈：Astro（内容集合驱动）、Mermaid（构建期渲成 SVG）、Cloudflare Pages（托管）
- 内容：Markdown 存于版本库，新增内容 = 加一个文件
- 身份与履历：结构化数据存于 `src/data/profile.ts`，首页身份区 / 技术栈 / 关于页 / 简历文本页共用同一份真源
- 简历双形态：`/resume` 文本页（可复制、可被检索）+ `/resume/pdf` 原件查看器，一致性由 `npm run resume-sync` 兜住
- 脱敏：构建前置扫描，命中禁出词即构建失败（详见 `GLOSSARY.md`）

## 快速开始

```bash
npm install
npm run dev          # 本地预览 http://localhost:4321
```

> `npm run dev` 会先自动生成图表 SVG（`predev` 钩子）——图源是 `diagrams/*.mmd`，
> 生成物不入库，克隆后无需手动准备。

## 命令

| 命令 | 作用 |
|---|---|
| `npm run dev` | 本地开发预览 |
| `npm run sanitize` | 只跑脱敏扫描（可单独用） |
| `npm run series` | 专栏校验：章节的专栏/分区/序号与 `src/series.ts` 一致 |
| `npm run import:series` | 外部长文导入为专栏章节（`-- --src <目录> [--skip-internal]`，详见 `SERIES.md`） |
| `npm run diagrams` | `diagrams/*.mmd` → `src/assets/diagrams/*.svg` |
| `npm run og` | 生成 OG 分享图 `public/og/default.png` |
| `npm run build` | 脱敏 → 口径 → 专栏 → PDF 审计 → 简历一致性 → 图表 → deck → OG → 静态构建（任一步失败即中止） |
| `npm run diagrams` | `diagrams/*.mmd` → `src/assets/diagrams/*.svg`（站内与 deck 共用同一批产物） |
| `npm run deck` | `deck/*` + 图表产物 → `public/deck/qa-agent/index.html`（自包含单文件，失败只告警） |
| `npm run pdf` | 用本机 Chromium 把项目页打印为 PDF（兜底产物，失败不阻塞） |
| `npm run pdf-audit` | 审计 `public/` 下所有待发布 PDF（PII + 禁出词）；加 `--dump` 可打印全文供人工通读 |
| `npm run resume-sync` | 简历双源校验：`profile.ts` 抽出的事实必须在 `resume.pdf` 里都能找到；加 `--dump` 打印事实清单 |
| `npm run check` | 链接与资源健康检查（无死链才通过） |
| `npm run mobile` | 用 CDP 强制 390px 真视口核对（`-- /projects/qa-agent --h 2400 --out _m.png`） |
| `npm run deploy` | 构建 → PDF → 再构建 → 检查 → 部署到 Cloudflare Pages |
| `npm run preview` | 本地预览已构建产物 |

## 目录结构

```
src/
  content/
    projects/*.md      项目页（/projects/<文件名>）
    posts/*.md         文章（/posts/<文件名>）；专栏章节也在其中，命名 claudecode-<NN>-<english>
  content.config.ts    内容集合与 frontmatter 校验
  series.ts            专栏登记表（标题 / 描述 / 分区顺序的唯一真源）
  data/profile.ts      身份 / 方向 / 技术栈 / 经历 / 教育 / 联系方式（首页与简历的共同真源）
  layouts/             站点骨架、项目页、文章页、专栏章节页布局
  components/          Prose（长文排版）、Mermaid（图表）、ShotPlaceholder（截图占位）
  pages/               首页、关于、文章列表、文章/章节详情、项目页、简历（resume/index 文本页、
                       resume/pdf 原件查看器）、404、robots
  plugins/             rehype-diagram：把 ![x](diagram:名) 就地换成内联 SVG；
                       rehype-base-links：给内容里的站内绝对链接补托管前缀（子路径托管不 404）
  styles/              theme.css（设计令牌）、typography.css（排版规范）
  site.config.ts       站点名 / 定位 / 联系方式 / 导航（发布前替换 TODO）
  utils/               路径解析、URL 前缀、内容查询、图表加载
diagrams/*.mmd         图表唯一真源（见 diagrams/README.md）
deck/                  扫读版 deck 源（slides.mjs 内容 + theme.css 样式 + nav.js 翻页）
assets/og/default.svg  OG 图源
scripts/               构建流水线脚本（脱敏 / 口径 / 专栏 / PDF 审计 / 图表 / deck / OG / PDF / 链接 / 手机核对 / 专栏导入）
public/                favicon、OG 产物、PDF 产物、deck 产物、简历 PDF
```

## URL 契约（勿改）

| 路径 | 用途 | 稳定性 |
|---|---|---|
| `/` | 首页（身份区 → 技术栈 → 精选项目 → 其他项目 → 文章 → 联系） | 稳定 |
| `/about` | 关于 | 稳定 |
| `/resume` | 简历文本页——**对外引用的简历入口** | **永不变更** |
| `/resume/pdf` | 简历 PDF 原件查看器（投递与打印形态的下载入口） | **永不变更** |
| `/projects/<slug>` | 项目页——**对外引用（简历等）指向此处** | **永不变更** |
| `/posts/<slug>` | 单篇文章（不含专栏章节） | 发布后不变更 |
| `/posts` | 文章列表（专栏卡片 + 单篇分页） | 稳定 |
| `/claudecode` | 专栏《Claude Code CLI 源码解析》目录 | 稳定 |
| `/claudecode/<NN>-<english>` | 专栏章节——slug 即 URL 契约，发布后不变更（见 `SERIES.md`） | 发布后不变更 |
| `/deck/<slug>` | 概览 deck（子项目，可为空） | 稳定 |

新增项目只追加 `/projects/<新slug>`；域名锚"人"、路径锚"项目"，对外链接不因新增内容失效。

## 内容写法

**项目页**（`src/content/projects/<slug>.md`）

```yaml
---
title: 项目名
tagline: 一句话说明（≤80 字）
summary: 列表页摘要
order: 1          # 越小越靠前
featured: true    # 是否进首页精选
tags: [标签]
updated: 2026-09-15
---
```

**文章**（`src/content/posts/<slug>.md`）

```yaml
---
title: 标题
summary: 摘要
publishedAt: 2026-09-15
tags: [标签]
---
```

`draft: true` 的内容不会进入构建产物。文件名即 URL slug。

**专栏章节**（`src/content/posts/claudecode-<NN>-<english>.md`）由 `npm run import:series` 生成，
frontmatter 另带 `series` / `seriesOrder` / `seriesGroup`（必须命中 `src/series.ts` 的分区）与
`readingMinutes` / `weight`。这三项由 `npm run series` 校验，**不要手改章节产物**（下次导入覆盖）。

**配图**：在 Markdown 里直接按名引用图源，构建期内联为 SVG（不依赖前端 JS）：

```markdown
![图注文字](diagram:flow-site-pipeline)
```

**截图占位**：Markdown 无法使用组件，需要占位时在页面模板里用
`<ShotPlaceholder id="S01" title="..." hint="..." />`，并同步登记 `SHOTS.md`。

## 图表

- 源文件放 `diagrams/*.mmd`，命名 `<语义前缀>-<主题>.mmd`，规范见 `diagrams/README.md`
- 一处绘制、多处复用：站内页面、deck、PDF 共用同一批 SVG
- 产物 `src/assets/diagrams/*.svg` 是生成物，**禁止手改**（下次构建覆盖）

## 脱敏与发布

- `GLOSSARY.md`：脱敏边界 + 指标区间化登记（**口径单一来源**）。禁出词表与泛化映射表**不入库** ——
  公开仓库登记内部标识，等于把泛化掉的内部名连同对照关系一并公开，泛化沦为摆设
- 词表两处来源：本地 `GLOSSARY.local.md`（gitignored）与仓库 secret `GLOSSARY_WORDS`，内容须一致；
  两处都取不到时闸门直接失败，不允许"取不到词表就当没命中"
- 构建前自动扫描 `src/**`、`diagrams/**`、`deck/**`、根级 `*.md`、`openspec/**`；命中即失败，漏网内容无法发布
  （规则文档与仓库文档同样公开 —— 早先词表就是从 `GLOSSARY.md` 与 `openspec/` 里漏出去的）
- 命中词在日志里打码：CI 日志同样是公开的；本地要看全文设 `SANITIZE_REVEAL=1`
- `npm run figures` 校验页面数字与登记表述逐字一致；两处不一致即失败，数字打架进不了线上
- `npm run pdf-audit` 把 `public/` 下的 PDF 抽成文本再跑同一套词表（PDF 是文本闸门扫不到的盲区），
  并叠加 PII 模式（手机号 / 身份证 / 固话）
- `npm run resume-sync` 校验简历两份内容源一致：站点侧事实（雇主、学校、学位、时间区间、指标精确值、邮箱）
  必须都能在 `public/resume/resume.pdf` 里找到，缺一条即失败；方向是单向包含，PDF 多出的内容不报错
- 发布前按 `RELEASE-CHECKLIST.md` 逐条自检，终审结论记入该文件
- 截图占位状态在 `SHOTS.md` 跟踪；简历上站清单在 `RESUME.md`；专栏导入边界与维护在 `SERIES.md`

## 部署

```bash
cp .env.example .env    # 填 SITE_URL / 凭据
npm run deploy
```

- 托管：Cloudflare Pages（免备案，可绑自定义域名）
- 路径前缀由 `BASE_PATH` 控制：根路径 `/`、子路径 `/xxx/`，换托管无需改源码
- 部署为无状态操作：回滚 = 重新部署上一版 `dist/`
- 凭据只从环境变量读取，绝不入库
- **deploy 里构建跑了两次**：PDF 与 deck 是由已构建的 `dist/` 产出的兜底/附加产物，
  项目页要按它们是否存在来决定是否出入口，因此必须在它们生成后再构建一次
  （第一次构建 → 产出 PDF → 第二次构建带上入口 → 检查 → 部署）

## GitHub Pages 部署

推送到 `main` 即自动构建并发布（`.github/workflows/deploy.yml`）。

- 站点前缀自动推导：仓库名为 `<用户>.github.io` 时用根路径，其它仓库用 `/<仓库名>/`
- **绑定自定义域名后必须覆盖前缀**：Pages 会把站点改到域名根路径，
  在 `Settings → Secrets and variables → Actions → Variables` 加两个变量即可：
  - `SITE_URL` = `https://你的域名`
  - `BASE_PATH` = `/`
- 四道闸门在 CI 上逐步执行，任何一道不过就不发布
- CI 上缺中文字体：PDF 与 OG 会**自动跳过**（仓库里已提交的产物兜底发布），
  不会因为环境缺字体卡住上线；改文案后在本机跑 `npm run pdf` / `npm run og` 更新产物并提交

## 视觉规范（勿绕过）

- 正文 ≥ 20px（目标 24px）、次级文字 ≥ 16px、行高 1.6~1.8、正文列宽 ≤ 72ch
- 一主色 + 灰阶，颜色一律走 `theme.css` 的 CSS 变量，禁止硬编码色值
- 约束落在 `typography.css` 与 `Prose.astro`，新增内容自动继承，不要写内联样式覆盖
