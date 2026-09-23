## 1. 仓库与脚手架

- [x] 1.1 创建个人私库 `personal-site`，初始化 git，加入 `.gitignore`（node_modules / dist / .astro / .env）
- [x] 1.2 初始化 Astro 项目（最新稳定版），生成独立 `package.json` + lockfile
- [x] 1.3 建立目录骨架：`src/{content,layouts,components,pages,styles}`、`diagrams/`、`public/`、`scripts/`
- [x] 1.4 配置 `astro.config.mjs`：站点 URL 参数化（读环境变量）、`BASE_PATH` 支持、静态输出模式
- [x] 1.5 验证隔离：确认本仓库不含任何雇主仓库路径引用（grep 内部域名 / 内部系统名零命中）

## 2. 内容模型与路由

- [x] 2.1 定义内容集合 schema（`projects` 与 `posts` 的 frontmatter：标题 / 摘要 / 日期 / 标签 / 顺序 / 草稿位）——实现在 `src/content.config.ts`（Astro 5+ 的新路径）
- [x] 2.2 实现 `BaseLayout.astro`：站点骨架（导航 / 页脚 / meta / OG / favicon）
- [x] 2.3 实现 `/` 首页：一句话定位 + 精选项目列表 + 文章索引
- [x] 2.4 实现 `/about` 关于我：长期名片，仅放邮箱与 GitHub 等可公开渠道
- [x] 2.5 实现 `/projects/[slug].astro` 与 `/posts/[slug].astro`：按 slug 渲染，404 兜底
- [x] 2.6 实现站点元信息：`sitemap.xml`、robots、favicon、OG 分享图
- [x] 2.7 加一篇样例内容（project + post 各一）跑通全链路，验证"新增内容 = 加一个 md 文件"

## 3. 排版规范落地

- [x] 3.1 建立 `theme.css`：一主色 + 灰阶双色系，全部以 CSS 变量令牌化
- [x] 3.2 建立 `typography.css`：正文 ≥20px（目标 24px）、行高、次级字号下限、正文宽度 ≤72ch
- [x] 3.3 实现 `Prose.astro` 长文容器：标题层级、列表、表格、引用、代码块的统一排版
- [x] 3.4 落实信息密度约束：列表项留呼吸感（每屏要点 ≤5 的视觉一半，另一半靠写作规范）、代码块按 15 行封顶滚动
- [x] 3.5 实现 `ShotPlaceholder.astro`：统一视觉的虚线占位框（标题 + 取景建议）
- [x] 3.6 建立 `SHOTS.md` 清单格式：编号 / 页面 / 标题 / 取景建议 / 脱敏注意 / 覆盖状态复选框
- [x] 3.7 手机端实测（CDP 设备模拟 390px 视口）：正文可读、导航可用、页面无横向滚动

## 4. 脱敏闸门

- [x] 4.1 编写 `GLOSSARY.md`：泛化映射表 + 禁出词表（精确词与正则模式两类）
- [x] 4.2 实现 `scripts/sanitize-check.mjs`：扫描 `src/**`（含 content 与 astro/ts）与 `diagrams/`，输出命中词与位置并以非零码退出
- [x] 4.3 接入构建硬前置，双路径实测：注入禁词 → 构建失败（整条链在第一步中止）；移除 → 构建通过
- [x] 4.4 定义指标区间化规则（精确内部数字 → 区间或倍数表述）并写进自检清单
- [x] 4.5 建立人工终审清单 `RELEASE-CHECKLIST.md`：按"假设雇主可见"标准通读 + `SHOTS.md` 逐条核对

## 5. 图表管线

- [x] 5.1 约定图源规范：`diagrams/<语义名>.mmd`，文件头注释写明用途与复用位置（见 `diagrams/README.md`）
- [x] 5.2 实现 `scripts/build-diagrams.mjs`：`.mmd` → SVG，输出 `src/assets/diagrams/`，进构建流水线
- [x] 5.3 实现图表内联：`Mermaid.astro`（.astro 侧）+ `src/plugins/rehype-diagram.mjs`（Markdown 侧，`![图注](diagram:名)` 就地内联 SVG，无运行时 JS）
- [x] 5.4 验证复用：同一图源在文章页与关于页引用同一 SVG，改动图源后两处同步更新
- [x] 5.5 验证禁改约束：SVG 为生成物（加入 .gitignore + 生成头注释 + 每次构建先清目录重建），手改会被覆盖

## 6. 构建与发布

- [x] 6.1 编写构建脚本：`sanitize-check` → `build-diagrams` → `build-og` → `astro build`，串为一条命令
- [x] 6.2 实现 `scripts/deploy.mjs`：部署 `dist/` 到 Cloudflare Pages，读取 token 与项目名自环境变量（不硬编码凭据）
- [x] 6.3 验证 `BASE_PATH`：根路径与子路径两种构建产物均资源加载正常（子路径下 favicon / 导航 / PDF 链接 / sitemap 全部带前缀）
- [x] 6.4 PDF 兜底：用本机 Chromium 打印项目页为 PDF，存 `public/pdf/` 与 `dist/pdf/`；验证"失败不阻塞站点发布"
- [x] 6.5 PDF 入口探测：产物存在才出下载链接，缺失显示"生成中"而非死链
- [x] 6.6 回滚验证：删掉 dist 后用上一版快照恢复，链接与资源检查仍全部通过

## 7. 验收与上线

- [x] 7.1 视觉走查：排版规范逐条对照（字号 / 密度 / 配色 / 宽度），桌面实测
- [x] 7.2 验收对照：`site-foundation` / `site-typography` / `sanitization-gate` / `diagram-pipeline` / `site-deploy` 五份规格逐条自检
- [x] 7.3 性能检查：全站 12 个文件共 280KB，**零 JS 文件**，无外部字体请求
- [x] 7.4 链接健康检查：`npm run check` 扫描全部页面的站内链接与资源，无 404 死链（根路径与子路径两态均通过）
- [x] 7.5 记录基线：目录结构、命令清单、URL 契约、写作与发布流程写入 `README.md`

## 8. 线下人工流程（非代码，与开发并行）

- [ ] 8.1 注册个人 GitHub 账号与 Cloudflare 账号
- [ ] 8.2 购买域名（锚个人标识，非项目名），开启自动续费
- [ ] 8.3 域名解析接入 Cloudflare，验证 HTTPS 与自定义域名可访问
- [ ] 8.4 可选长期项：ICP 备案提交（改善国内访问）；完成后仅换解析，源码无需改动
