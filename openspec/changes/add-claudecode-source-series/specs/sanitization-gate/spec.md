## ADDED Requirements

### Requirement: 外部源导入边界

站点 SHALL 在内容层落实外部源导入的边界：导入脚本（`scripts/import-series.mjs`）MUST 显式声明来源号段白名单与内部内容黑名单，黑名单命中时 MUST 非零退出；导入产物 MUST 位于 `src/content/` 之下，从而纳入既有构建前置脱敏扫描范围。仓库 MUST NOT 保存源文档本体或其绝对路径。

#### Scenario: 导入产物纳入扫描

- **WHEN** 执行构建
- **THEN** 导入产生的文章文件与人工撰写的文章使用同一套禁出词扫描，不存在豁免

#### Scenario: 内部内容被误导入

- **WHEN** 导入产物中含禁出词
- **THEN** 构建 MUST 失败，产物 MUST NOT 上线

#### Scenario: 导入产物不得携带源路径

- **WHEN** 审查导入产物的 frontmatter 与正文
- **THEN** 不含源目录绝对路径，只保留源文件名与内容摘要

### Requirement: 导入产物的人工确认

每次导入后、发布前 SHALL 做一次人工确认：抽查章节的改写结果（内网标识、互链、元数据）并记录结论，确认项 SHALL 落在 `RELEASE-CHECKLIST.md`。

#### Scenario: 首次导入完成

- **WHEN** 23 篇章节首次导入并构建通过
- **THEN** `RELEASE-CHECKLIST.md` 中存在本次导入的抽查记录（抽查篇目与结论）

#### Scenario: 内容合规确认

- **WHEN** 发布拆解类内容
- **THEN** 发布清单中存在一条关于该材料可公开性的作者确认项
