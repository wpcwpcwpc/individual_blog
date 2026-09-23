## ADDED Requirements

### Requirement: 专栏登记表

站点 SHALL 在 `src/series.ts` 维护专栏登记表，作为专栏元数据的单一真源：专栏 id、标题、一句话描述、分区（有序）与分区说明。分区顺序 MUST 由登记表决定，MUST NOT 由内容文件命名或目录顺序推导。

#### Scenario: 专区页读取专栏信息

- **WHEN** 渲染专区页或文章列表页
- **THEN** 专栏标题、描述与分区顺序全部来自 `src/series.ts`，页面内不写死分区数组

#### Scenario: 登记表缺失某专栏 id

- **WHEN** 某篇文章声明的 `series` 值在登记表中不存在
- **THEN** 构建 MUST 失败（不允许出现"无主章节"）

### Requirement: 专栏章节元数据

文章集合 SHALL 支持专栏字段：专栏 id、章节序号（数值，决定顺序）、分区名、阅读时长（分钟）、重要程度（星级）。章节序号 MUST 在该专栏内唯一。

#### Scenario: 章节按序号排序

- **WHEN** 专区页列出某分区下的章节
- **THEN** 章节按序号升序展示，序号缺失的章节 MUST NOT 出现在专区页

#### Scenario: 序号重复

- **WHEN** 同一专栏内出现重复的章节序号
- **THEN** 构建 MUST 失败并在报错中列出冲突的两篇文章

### Requirement: 专区页

站点 SHALL 提供 `/claudecode` 专区页，展示专栏标题、一句话描述、篇数统计，并按登记表的分区顺序分组列出全部章节（章节序号、标题、阅读时长、重要程度）。

#### Scenario: 访问专区页

- **WHEN** 读者访问 `/claudecode`
- **THEN** 页面按分区顺序列出全部已发布章节，且每个章节链接指向其章节页

#### Scenario: 空分区

- **WHEN** 某分区下没有任何章节
- **THEN** 该分区 MUST NOT 渲染（不出现空标题）

### Requirement: 章节页

站点 SHALL 提供章节详情页 `/claudecode/<slug>`，展示章节正文，并在页面上标明所属专栏、分区、章节序号、阅读时长与重要程度，同时提供专栏内上一篇/下一篇导航。

#### Scenario: 章节页导航

- **WHEN** 读者打开某章节页
- **THEN** 页面给出同专栏内相邻章节的链接，首篇无"上一篇"、末篇无"下一篇"

#### Scenario: 章节页可回到专区

- **WHEN** 读者在任意章节页
- **THEN** 页面提供返回专区页的入口

### Requirement: 章节 slug 稳定性

章节 slug SHALL 由导入脚本内的显式映射表（章节序号 → slug）生成，格式为 `claudecode-<NN>-<english>`。slug 一经发布 MUST NOT 变更；映射表 MUST NOT 由标题机器翻译推导。

#### Scenario: 源文档新增未映射章节

- **WHEN** 源目录出现映射表中没有的章节序号
- **THEN** 导入脚本 MUST 报错退出，MUST NOT 自动生成 slug

#### Scenario: 标题措辞调整

- **WHEN** 章节标题在源文档中被改写
- **THEN** slug 保持原值不变
