## MODIFIED Requirements

### Requirement: URL 拓扑稳定性

站点 SHALL 遵循固定 URL 契约：`/` 首页、`/about` 关于我、`/resume` 简历文本页、`/resume/pdf` 简历 PDF 形态、`/projects/<slug>` 项目页、`/posts/<slug>` 文章页、`/deck/<slug>` 概览 deck。`/projects/<slug>` SHALL 作为对外引用路径（简历等），一经发布 MUST NOT 变更；`/resume` 与 `/resume/pdf` 一经发布同样 MUST NOT 变更（PDF 形态只允许在 `/resume` 之下换位置，不允许消失）。

#### Scenario: 新增第二个项目

- **WHEN** 站点已有项目 `/projects/qa-agent`，此时新增另一个项目
- **THEN** 新项目使用新的 `/projects/<新slug>` 路径，既有路径与域名均不变更

#### Scenario: 变更既有项目 slug

- **WHEN** 有人试图修改已发布项目的 slug
- **THEN** 该变更 MUST 被拒绝，或必须同时提供旧路径的重定向，以保证外部引用不失效

#### Scenario: 调整简历形态归属

- **WHEN** 需要把简历主入口在文本形态与 PDF 形态之间切换
- **THEN** 两个路径都保持可达并互相链接，MUST NOT 让任一形态返回 404

## ADDED Requirements

### Requirement: 首页区块契约

首页 SHALL 由固定区块构成：身份区、技术栈、精选项目、其他项目、文章、联系。区块顺序 SHALL 稳定，且 MUST NOT 因为内容集合为空而整段消失（空内容显示占位提示）。

#### Scenario: 无精选项目

- **WHEN** 内容集合中没有任何标记为精选的项目
- **THEN** 该区块显示占位提示，身份区与技术栈区仍正常展示

#### Scenario: 站点结构后续调整

- **WHEN** 因新增内容形态需要调整首页
- **THEN** 既有区块次序保持不变，仅允许在契约之外追加新块
