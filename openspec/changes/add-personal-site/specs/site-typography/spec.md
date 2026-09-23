## ADDED Requirements

### Requirement: 排版规范落在样式层

站点 SHALL 把排版约束实现在 CSS 与布局组件中（`src/styles/typography.css`、`src/styles/theme.css`、`Prose.astro`），新增内容 MUST 自动继承这些约束，MUST NOT 依赖作者手工遵守。

#### Scenario: 新增一篇文章

- **WHEN** 新增文章只填写内容、不写任何内联样式
- **THEN** 该文章字号、行高、宽度、间距与既有文章完全一致

#### Scenario: 内容里写内联字号覆盖

- **WHEN** 内容中出现内联样式试图把字号调到规范下限之下
- **THEN** 该做法 MUST 被拒绝（样式层以规范为准，不走内容级覆盖）

### Requirement: 正文可读性下限

正文正文字号 SHALL NOT 小于 20px（目标 24px），次级文字 SHALL NOT 小于 16px，正文行高 SHALL 为 1.6~1.8，正文文本列宽 SHALL NOT 超过 72ch。

#### Scenario: 手机端阅读长文

- **WHEN** 在手机浏览器打开任一文章页
- **THEN** 正文无需双指放大即可舒适阅读，且不出现横向滚动

#### Scenario: 宽屏阅读长文

- **WHEN** 在宽屏显示器打开长文
- **THEN** 正文列宽受限不铺满屏，行长保持在 72ch 以内

### Requirement: 信息密度约束

单屏要点 SHALL NOT 超过 5 条，代码块 SHALL NOT 超过 15 行且仅用于架构示意，MUST NOT 出现真实业务代码。

#### Scenario: 写一段架构说明

- **WHEN** 页面某段需要列出多个要点
- **THEN** 要点数被控制在 5 条以内，超出部分拆到下一页或改写为叙述段落

#### Scenario: 需要展示代码

- **WHEN** 内容想展示一段实现细节
- **THEN** 换成架构示意（图或伪代码），不贴真实业务代码

### Requirement: 配色体系

站点 SHALL 采用一主色 + 灰阶的双色系，颜色 SHALL 以 CSS 变量令牌定义，MUST NOT 在组件或内容中硬编码颜色值。

#### Scenario: 调整主色

- **WHEN** 设计师或本人想换主色
- **THEN** 只需修改 `theme.css` 中的令牌值，全站颜色同步变化
