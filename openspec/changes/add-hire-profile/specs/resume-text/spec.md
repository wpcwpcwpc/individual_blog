## ADDED Requirements

### Requirement: 简历文本形态

站点 SHALL 在 `/resume` 提供可选中、可复制的简历文本页，内容渲染自 `src/data/profile.ts`。文本页 SHALL 使用语义化标签（分节标题、列表）表达经历与教育，MUST NOT 用纯视觉表格承载履历条目。PDF 原件 SHALL 仍可从文本页下载。

#### Scenario: 招聘方复制经历

- **WHEN** 招聘方在 `/resume` 选中一段经历并复制
- **THEN** 得到纯文本内容，无需先下载 PDF

#### Scenario: 搜索引擎与 ATS 抓取

- **WHEN** 爬虫或招聘系统抓取 `/resume`
- **THEN** 页面正文包含姓名、方向、技术栈、经历、教育与联系方式的可解析文本

### Requirement: PDF 形态保留

PDF 查看器 SHALL 保留在 `/resume/pdf`，行为与既有实现一致（pdf.js 逐页渲染、可视区懒渲染、渲染失败回退浏览器内置阅读器、无 JS 时走 `<noscript>` 兜底）。两个页面 SHALL 互相提供跳转入口。

#### Scenario: 邮件投递场景

- **WHEN** 需要以附件形式投递简历
- **THEN** 可从文本页进入 PDF 页下载原件

#### Scenario: PDF 缺失

- **WHEN** `public/resume/resume.pdf` 不存在
- **THEN** 文本页仍完整可用，PDF 入口显示不可用提示而非指向不存在的文件

### Requirement: 双源一致性校验

构建 SHALL 执行简历一致性校验：从 `public/resume/resume.pdf` 提取文本，断言站点侧关键事实（雇主名、学校、学位、时间区间、指标精确值）在 PDF 文本中均出现；缺失时构建 MUST 失败并列出缺失条目。校验方向 SHALL 为单向包含（PDF ⊇ 站点侧事实），MUST NOT 要求 PDF 中的全部内容都出现在站点侧。

#### Scenario: 换了新 PDF 但漏改站点

- **WHEN** 简历 PDF 更新后，某条站点侧关键事实在新 PDF 中已不存在
- **THEN** 构建失败并指出缺失的事实，MUST NOT 静默发布不一致的简历

#### Scenario: 站点侧刻意只展示子集

- **WHEN** 站点侧只写了 PDF 中的部分经历
- **THEN** 校验通过，MUST NOT 因"站点内容少于 PDF"而失败

### Requirement: PDF 为投递形态，站点为阅读形态

`RESUME.md` SHALL 记录双形态分工：PDF 由人工产出、作为投递与打印形态；站点文本页由 `profile.ts` 渲染、作为在线阅读形态。该文档 MUST NOT 再声明"PDF 是唯一内容源"。

#### Scenario: 修改简历内容

- **WHEN** 需要更新简历中的一条经历
- **THEN** 同时更新 `profile.ts` 与简历 PDF 原件，构建校验通过后才可发布

#### Scenario: 文档与实现不一致

- **WHEN** `RESUME.md` 的描述与页面实际形态不符
- **THEN** 以页面与规格为准修正文档，避免后来者按过期约定操作
