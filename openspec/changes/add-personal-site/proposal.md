## Why

需要一个**长期**承载个人项目与技术写作的门面站点，简历链接只是它的第一个用途。当前零地基：无域名、无站点、无个人代码仓库。

一次性求职物料的思路不适用于这个目标，会造成两种不可逆损失：

- **源码寄放在雇主内部仓库** → 离职或权限回收后源码失联，只剩构建产物，无法维护，站点必然烂掉
- **载体选为演示工具（如 slide deck）** → 无法承载长期内容增长，也无法承载技术写作

同时，站点若要在长期内存活，URL 必须稳定：域名锚"人"、路径锚"项目"，后续新增项目只追加路径，简历上已发布的链接永不失效。

## What Changes

- 新建独立仓库 `personal-site`（个人 GitHub 私库），与任何雇主仓库完全隔离，独立 `package.json` + lockfile
- 站点基于 **Astro** 静态构建，内容集合驱动：`projects`（项目页）与 `posts`（技术文章），Markdown 源存 git
- **URL 拓扑约定**：`/` 首页、`/about` 关于我、`/projects/<slug>` 项目页（简历引用路径，永不变更）、`/posts/<slug>` 文章
- **排版规范落地为代码**：`src/styles/typography.css`，正文字号、信息密度、配色、导航等约束在样式层强制，不依赖口头约定
- **脱敏闸门**：`GLOSSARY.md`（泛化映射表 + 禁出词表）与 `scripts/sanitize-check.mjs`（构建前置扫描，命中即构建失败），配套人工终审清单
- **图表管线**：图源存 `diagrams/*.mmd`，构建期转 SVG，一处绘制、站内多页复用，并可导出供 PDF / deck 使用
- **部署链路**：Cloudflare Pages + 自定义域名，构建参数 `BASE_PATH` 参数化（换域名 / 换托管无需改源码）
- **PDF 兜底产物**：站点内容的 PDF 导出版，作为离线阅读与附件场景的降级方案，导出失败不阻塞站点发布
- `about` 页与首页作为长期名片与索引；联系信息仅放邮箱等可公开渠道

## Capabilities

### New Capabilities

- `site-foundation`: 仓库与站点骨架——Astro 项目结构、内容集合模型（projects/posts frontmatter 契约）、路由约定、站点元信息（favicon / OG / sitemap）
- `site-typography`: 排版与视觉规范——字号下限、信息密度、配色体系、导航与页码等约束在样式层强制
- `sanitization-gate`: 脱敏闸门机制——禁出词表文件契约、构建前置扫描器行为、命中即失败、人工终审清单、指标区间化规则
- `diagram-pipeline`: 图表管线——图源格式与存放约定、构建期转 SVG、多页复用、禁止内部截图
- `site-deploy`: 构建与发布——Cloudflare Pages 部署、自定义域名与 `BASE_PATH` 参数化、PDF 导出非阻塞、回滚方式

### Modified Capabilities

（无——全新仓库，无既有规格）

## Impact

- 新建个人仓库 `personal-site`，新增顶层目录结构（见 `design.md` D2）；**不进入任何雇主仓库**，不触碰雇主子系统与其打包清单
- 素材来源为雇主内部仓库的规格与文档，但**仅经人工搬运与脱敏后**写入本站；本仓库不引用内部仓库路径
- 新增离线人工流程（不产出代码，进 tasks 手动项）：注册个人 GitHub 账号、注册 Cloudflare 账号、购买域名、可选 ICP 备案
- 运行成本：仅域名年费（约 ¥60~80/年），托管与构建免费
- 风险：脱敏漏网（NDA 风险）→ 由 `sanitization-gate` 规格的构建硬拦截 + 人工终审双闸门控制；依赖第三方托管 → 静态产物可随时迁走，无供应商锁定
