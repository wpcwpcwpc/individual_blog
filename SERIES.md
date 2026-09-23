# SERIES —— 专栏维护与外部长文导入

专栏是把成体系的长文（如整套源码拆解）作为一个整体呈现的形态：专区页给入口与章节顺序，
章节页给正文与上/下篇导航。当前只有一个专栏：`claudecode`（Claude Code CLI 源码解析，23 章）。

> 本文件含导入边界与源侧问题清单，仅用于维护；MUST NOT 复制进 `public/` 或进入部署产物。

## 一、形态与路径

| 东西 | 位置 | 说明 |
|---|---|---|
| 专栏登记表 | `src/series.ts` | 专栏标题、描述、分区顺序的唯一真源 |
| 章节内容 | `src/content/posts/claudecode-<NN>-<english>.md` | 由脚本导入，不手写 |
| 专区页 | `/claudecode` | 按分区列章节（序号 / 标题 / 时长 / 星级） |
| 章节页 | `/claudecode/<NN>-<english>` | 文件名里的 `claudecode-` 前缀在 URL 中去掉 |
| 导入脚本 | `scripts/import-series.mjs` | 只读源目录；一次性导入，可重跑 |

章节**不在** `/posts/<id>` 出页（避免同一内容两个 URL），也不出现在 `posts` 的单篇列表里。

章节正文里的站内互链写成根路径（`/claudecode/03-query-engine`）即可：构建期由
`src/plugins/rehype-base-links.mjs` 按 `BASE_PATH` 补前缀，换托管/绑域名都不用改内容。

## 二、导入怎么做

```bash
npm run import:series -- --src <文档目录> --skip-internal
```

- `--src` 必填，**每次由执行者传入**：源目录路径不写进仓库（站点是公开仓库）
- `--skip-internal` 只在源目录里同时存在内部号段（25–28）时才需要显式加上；
  不加则默认中止（内部内容的默认处置是"不导入"）
- 导入后照常 `npm run build`：脱敏 / 专栏 / 链接三道闸门会复核产物

脚本每次都会打印三份需要人看的东西：

| 报告项 | 含义 | 处理 |
|---|---|---|
| 内网标识改写 N 处 | 规则表命中的内网 IP / 端口 / 路径 / 域名 / 工号 | 抽查改写结果是否通顺 |
| 未解析的章节引用 | 指向已归档文档的引用，已降级为纯文本 | 确认降级合理，或补登记对应章节 |
| 代码围栏不配对 | 源文档少写一个开 ```` ``` ````，紧随的正文会被当成代码块 | 在**源文档**补围栏后重跑；导入不改写源文档 |

## 三、章节登记表（slug 即 URL 契约）

`scripts/import-series.mjs` 里的 `CHAPTERS` 是唯一来源：章节序号 → slug 短名 + 分区。
**slug 一经发布不得变更**；源文档改了标题，slug 不动。

| 分区 | 章节 |
|---|---|
| 导读 | 00 reading-guide |
| 核心架构 | 01 project-overview · 02 data-flow-lifecycle · 03 query-engine · 04 message-system · 05 prompt-engineering |
| 工具与能力扩展 | 06 tool-system · 07 core-tools · 08 mcp-integration |
| Agent 核心系统 | 09 agent-system · 10 agent-memory-state · 11 agent-collaboration · 12 builtin-agents |
| 安全与可观测性 | 13 git-integration · 15 observability · 16 security-sandbox · 17 extension-plugins |
| 工程支撑 | 18 multimodal · 19 testing-quality · 20 build-packaging · 21 config-environment · 23 code-style · 24 recipes-extension |

编号 14、22 在源材料里本就是空号（`npm run series` 每次都会提示）。

## 四、加一章 / 改一章

1. 在 `CHAPTERS` 里登记新号段的 slug 与分区（未登记号段脚本会直接报错，不猜 slug）
2. 源文档就位后重跑导入命令
3. `npm run build` 过闸门，`npm run mobile -- /claudecode/<新slug>/` 看一眼窄屏
4. 章节的 frontmatter 由脚本生成，**不要手改**——下次导入会覆盖

## 五、边界与已知问题

- **硬边界**：25–28 号段（公司内部工程内容）MUST NOT 进入仓库；导入脚本黑名单命中即中止
- 导入产物的 frontmatter 记 `source`（仅文件名）与 `sourceSha256`（前 12 位），用于判断源是否漂移；
  源文档更新后是否重导由作者决定
- 现有 `resume.pdf` 与拆解材料的免责口径见 `RESUME.md` 与 `RELEASE-CHECKLIST.md` 第六节
- 第 12 章源文档缺一个开围栏，导致其中两行正文被渲染成代码块（已在导入报告中标出）；
  作者在源侧补上后重跑导入即可修复
