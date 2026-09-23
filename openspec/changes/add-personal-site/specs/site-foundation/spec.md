## ADDED Requirements

### Requirement: 仓库与站点骨架

站点 SHALL 为独立 Astro 静态项目，拥有独立 `package.json` 与 lockfile，且 MUST NOT 引用任何雇主仓库的路径、依赖或构建产物。站点 SHALL 采用静态输出模式，产物为纯静态文件（无服务端运行时）。

#### Scenario: 全新环境克隆后构建

- **WHEN** 在新机器上克隆本仓库并执行安装与构建
- **THEN** 构建成功产出静态站点，过程中不访问任何雇主内部系统或私有源

#### Scenario: 新增一个项目页

- **WHEN** 在 `src/content/projects/` 下新增一个 Markdown 文件并填入合法 frontmatter
- **THEN** 站点自动生成对应的 `/projects/<slug>` 页面，无需修改任何路由代码

### Requirement: 内容集合模型

站点 SHALL 以 content collection 管理内容，至少包含 `projects`（项目）与 `posts`（文章）两个集合，各自 frontmatter schema SHALL 在 `src/content/config.ts` 中声明并由构建期校验。

#### Scenario: frontmatter 缺必填字段

- **WHEN** 某内容文件的 frontmatter 缺少必填字段（如标题）
- **THEN** 构建失败并指出出错文件与缺失字段，MUST NOT 静默生成残缺页面

#### Scenario: 草稿内容

- **WHEN** 某内容文件标记为草稿
- **THEN** 该内容不出现在生产构建产物中（列表与详情页均不可访问）

### Requirement: URL 拓扑稳定性

站点 SHALL 遵循固定 URL 契约：`/` 首页、`/about` 关于我、`/projects/<slug>` 项目页、`/posts/<slug>` 文章页、`/deck/<slug>` 概览 deck。`/projects/<slug>` SHALL 作为对外引用路径（简历等），一经发布 MUST NOT 变更。

#### Scenario: 新增第二个项目

- **WHEN** 站点已有项目 `/projects/qa-agent`，此时新增另一个项目
- **THEN** 新项目使用新的 `/projects/<新slug>` 路径，既有路径与域名均不变更

#### Scenario: 变更既有项目 slug

- **WHEN** 有人试图修改已发布项目的 slug
- **THEN** 该变更 MUST 被拒绝，或必须同时提供旧路径的重定向，以保证外部引用不失效

### Requirement: 站点元信息

站点 SHALL 提供 favicon、页面 meta 描述、OG 分享信息与 `sitemap.xml`，使链接在社交与聊天工具中展示可辨识的标题、摘要与缩略图。

#### Scenario: 链接被粘贴到聊天工具

- **WHEN** 把 `/projects/<slug>` 链接粘贴到支持 OG 预览的工具
- **THEN** 预览显示该页标题、摘要与分享图，而非站点默认占位信息

### Requirement: 内容 evergreen 约束

站点内容 SHALL 使用长期有效表述，MUST NOT 包含时效性文案（如"正在求职""近期更新"之类仅在特定时期成立的表述）。

#### Scenario: 求职期撰写内容

- **WHEN** 撰写项目页与文章内容
- **THEN** 文本在求职期结束后仍然是成立的门面描述，无需改写
