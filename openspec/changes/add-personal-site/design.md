## Context

- 需求背景见 `proposal.md`：为长期技术门面搭静态站点，简历链接是首个用途
- 已确认的路线决策（探索阶段冻结）：形态为"站点为主体 + deck 降为一页"；技术栈 Astro；源码归个人私库；托管 Cloudflare Pages + 自有域名；URL 域名锚人路径锚项目；图源独立存放多处复用；脱敏双闸门；Agent 口径取注册数 18；记录随源码走个人库
- 约束：站点是长期资产，URL 稳定性优先于任何短期便利；零后端零数据库；成本仅域名年费

## Goals / Non-Goals

**Goals:**

- 一个可长期演进的静态站点地基：新增项目页或文章只需加一个 Markdown 文件
- 排版规范可执行：约束落在 CSS 与布局组件里，新增内容自动继承，不靠人肉遵守
- 脱敏自动化：构建前扫描，命中禁出词即失败——漏网内容无法被发布出去
- 图表一处绘制多处复用：站内页面 / deck / PDF 共用同一批图源
- 部署可重复：一条命令构建、一条命令发布；换域名或换托管不需要改源码
- 简历引用路径 `/projects/<slug>` 永久稳定

**Non-Goals:**

- 交互式架构图（点击节点看详情）——后续演进方向，本期不做
- 评论、访问统计、搜索、多语言、CMS 后台
- 任何服务端能力（API / 数据库 / 鉴权）
- 真实内部截图的使用（一律重绘或占位）
- ICP 备案实施（可选线下事项，不进本期代码范围）

## Decisions

### D1 技术栈：Astro（备选 VitePress / Hugo / Next.js）

- **Astro**：内容优先（content collections 原生支持，Markdown 即内容）、默认零 JS（静态产物轻、手机端快）、需要交互时可用 island 挂载组件（未来"可点架构图"直接支持，不推倒重来）、SEO/sitemap/RSS 皆有官方集成。选它。
- VitePress：上手最快，但整体是"文档站"气质，做个人门面偏硬，博客与项目页混合场景别扭。弃。
- Hugo：构建最快、主题多，但模板语法（Go template）改视觉门槛高，长期改样式成本大。弃。
- Next.js：团队熟练（QA 平台前端即 React/TS），但对纯静态内容站过重，SSR/服务端能力用不上。弃。
- 版本：安装时取最新稳定版，锁定在 `package.json` + lockfile。

### D2 仓库与目录结构

```
personal-site/                      # 个人 GitHub 私库，唯一真源
  astro.config.mjs
  package.json                      # 独立 lockfile，与任何雇主项目无关
  src/
    content/
      projects/*.md                 # 项目页（frontmatter + 正文）
      posts/*.md                    # 技术文章
    layouts/
      BaseLayout.astro              # 站点骨架：导航 / 页脚 / meta / OG
      ProjectLayout.astro
      PostLayout.astro
    components/
      Mermaid.astro                 # 图渲染（读 diagrams 产物或源码）
      ShotPlaceholder.astro         # 截图占位（统一视觉的虚线框）
      Prose.astro                   # 长文排版容器
    pages/
      index.astro                   # 首页：定位 + 精选项目 + 文章索引
      about.astro                   # 长期名片（邮箱 / GitHub，不放手机号）
      projects/[slug].astro
      posts/[slug].astro
    styles/
      typography.css                # 排版规范落地处（见 D5）
      theme.css                     # 配色令牌
  diagrams/                         # 图源 *.mmd（一处绘制，多处复用，见 D6）
  public/
    favicon.svg
    og/                             # 社交分享图
    pdf/                            # PDF 兜底产物（见 D9）
  scripts/
    build-diagrams.mjs              # .mmd → SVG
    sanitize-check.mjs              # 脱敏扫描（见 D7）
    deploy.mjs                      # 发布（见 D8）
  GLOSSARY.md                       # 泛化映射表 + 禁出词表
  SHOTS.md                          # 截图占位覆盖清单
```

理由：内容（`src/content`）、图源（`diagrams`）、样式规范（`src/styles`）、流水线（`scripts`）四类资产物理分离——各自独立演进，互不牵连。

### D3 仓库归属：个人私库为唯一真源

- 站点源码、文章、图源、OpenSpec 记录**全部只存在于个人私库**；雇主内部仓库不留任何站点文件（零留痕）
- 素材获取路径：人工从内部仓库阅读 → 重写（非复制）→ 脱敏 → 写入本站
- 理由：门面必须锚在个人身上。源码寄放雇主仓库会导致离职后失联、站点无法维护
- 副产品：构建产物只从本站源码产生，内部内容误发布的风险面收敛到"人工搬运"这一道，而它又由 D7 的扫描硬拦截兜住

### D4 内容模型与 URL 拓扑

- 两条 content collection：`projects`（项目）与 `posts`（文章），各自 frontmatter schema 在 `src/content/config.ts` 声明并做类型校验
- URL 约定（**稳定契约**）：

| 路径 | 语义 | 稳定性 |
|---|---|---|
| `/` | 首页：一句话定位 + 精选项目 + 文章索引 | 稳定 |
| `/about` | 长期名片 | 稳定 |
| `/projects/<slug>` | 单个项目页——**简历直接引用此路径** | **永不变更** |
| `/posts/<slug>` | 单篇文章 | 发布后不变更 |
| `/deck/<slug>` | 该项目的概览 deck 静态产物 | 稳定（可后补） |

- 域名锚人、路径锚项目：新增项目只追加 `/projects/<新slug>`，既有路径与域名不动 → 简历链接寿命 = 域名寿命
- 内容约束：一律 evergreen 表述，不写时效性文案（"正在找工作"之类），使站点在求职期之外仍是有效门面

### D5 排版规范落在样式层

- 字体栈：`system-ui, "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif`
- 约束数字（落在 `typography.css`，新增内容自动继承）：

| 项 | 约束 |
|---|---|
| 正文 | ≥ 20px（手机可读下限），目标 24px 行高 1.7 |
| 次级文字 | ≥ 16px |
| 每屏要点 | ≤ 5 条 |
| 代码块 | ≤ 15 行，仅用于架构示意（非真实业务代码） |
| 配色 | 一主色 + 灰阶双色系，主色以 CSS 变量令牌化 |
| 宽度 | 正文最大宽度 ≤ 72ch，避免长行难读 |

- 视觉一致性：`Prose.astro` 统一长文排版，`ShotPlaceholder.astro` 统一占位视觉
- 深色模式：本期可选（用 CSS 变量令牌已在 D5 预留，实现成本低）

### D6 图表管线：一处绘制，多处复用

- 图源统一存 `diagrams/*.mmd`（Mermaid），命名带语义前缀（如 `arch-overview.mmd`、`flow-eval.mmd`）
- `scripts/build-diagrams.mjs` 把 `.mmd` 转成 SVG 输出到 `src/assets/diagrams/`，构建期执行
- 复用方式：站内文章用 `Mermaid.astro` 引用；deck 与 PDF 直接取同一批 SVG → **同一张图只有一个真源**，改动不会三处不同步
- 渲染策略：构建期渲染为内联 SVG（无运行时 JS、无 FOUC、可被搜索引擎读到）；不采用客户端 mermaid 运行时
- **禁止**内部系统截图：所有架构图一律重绘（含去内部系统名、去真实域名与端口、去真实文件路径）

### D7 脱敏闸门：机制

- `GLOSSARY.md` 两部分：**泛化映射表**（内部系统/产品 → 对外泛称）与**禁出词表**（精确词 + 正则模式两类）
- `scripts/sanitize-check.mjs`：扫描 `src/content/`、`src/**/*.astro`、`diagrams/`，命中禁出词即打印命中词与位置并非零退出
- 接入 `prebuild`（npm script 钩子）→ 扫描不通过则构建失败 → **漏网内容物理上无法被发布**
- 扫描器只负责禁词；指标区间化（精确数字 → 区间/倍数）靠内容规范 + 人工终审，写进 `sanitization-gate` 规格的 WHEN/THEN
- 人工终审：按"假设前雇主与现雇主都能看到"的标准通读全文，逐条核对 `SHOTS.md`
- 双闸门含义：机器闸门（自动化、可回归）+ 人闸门（语义判断、兜底）

### D8 部署：Cloudflare Pages + 自定义域名

- 构建：`npm run build` → `dist/`；发布：`scripts/deploy.mjs` 调用 Cloudflare Pages（或由 Git 集成自动构建）
- **免备案路径优先**：Cloudflare Pages 免费、支持自定义域名与全球 CDN、无需 ICP 备案 → 域名到手当天即可上线
- `BASE_PATH` 参数化：构建接受环境变量决定资源前缀（根路径 `/` 或子路径 `/xxx/`），换托管、换域名、切国内 CDN 均不需改源码
- 备案为可选长期优化（改善国内访问速度），不阻塞上线；备案完成后只需换解析与可能的 `BASE_PATH`，站点源码不变
- 回滚：静态产物无状态，回退 = 重新部署上一版 `dist/` 或云端的上一次部署记录

### D9 PDF 兜底产物

- 站点内容导出 PDF 存 `public/pdf/`，供离线阅读、邮件附件、纸质简历二维码场景
- **非阻塞**：PDF 导出失败不得阻塞站点构建与发布；页面上的下载入口需探测产物是否存在，缺失时显示"生成中"而非死链
- 导出实现细节（Playwright / 浏览器打印）在实施时定，规格只约束"存在性与非阻塞行为"

## Risks / Trade-offs

- **[R1] 脱敏漏网（NDA 红线）** → 构建前置硬拦截 + 人工终审双闸门；内容全部重写而非复制；图一律重绘
- **[R2] Cloudflare 国内访问速度一般** → 可接受（面试官多为国内但静态页体积小）；长期可备案 + 国内 CDN 迁移，`BASE_PATH` 已参数化
- **[R3] Node 工具链版本要求** → 独立仓库独立 lockfile，与任何雇主项目隔离，互不影响
- **[R4] 图源与站内引用不同步** → SVG 由 `.mmd` 单向生成，禁止手改 SVG；`build-diagrams.mjs` 进构建流水线
- **[R5] 长期演进导致结构腐化** → URL 契约（D4）与目录分层（D2）写进规格；新增内容只增不改既有路径
- **[R6] 站点无人维护而烂掉** → evergreen 内容策略 + 零后端零数据库 + 静态托管，维护成本趋近于零
- **[R7] 域名过期导致链接失效** → 域名自动续费开启；`/projects/<slug>` 与域名解耦，必要时可换域名平滑迁移

## Open Questions

- 具体域名候选与注册商（建议锚个人标识而非项目名，如 `yourname.dev` / `yourname.me`）
- 是否启用 RSS / 站点内搜索（内容规模小，本期可不做）
- 深色模式是否本期实现（CSS 变量已预留）
- 部署方式：Cloudflare Git 集成自动构建 vs 本地 `deploy.mjs` 手动发布（前者省事，后者可控）
