## ADDED Requirements

### Requirement: 图源独立存放

所有图表 SHALL 以 Mermaid 源文件形式存放于顶层 `diagrams/` 目录，文件名 SHALL 带语义前缀（如 `arch-`、`flow-`、`seq-`），MUST NOT 把图表源码直接内联在内容文件里作为唯一副本。

#### Scenario: 需要一张新的架构图

- **WHEN** 某页面需要一张架构图
- **THEN** 先在 `diagrams/` 新增 `arch-*.mmd` 源文件，再由内容引用，不直接在页面里写图

#### Scenario: 同一张图被两处用到

- **WHEN** 两个页面（或页面与 deck / PDF）需要同一张图
- **THEN** 两处引用同一图源产出的同一 SVG，MUST NOT 各自维护一份副本

### Requirement: 构建期转 SVG

图表 SHALL 在构建期由 `scripts/build-diagrams.mjs` 从 `.mmd` 转换为 SVG，作为构建流水线的固定步骤。站点 SHALL NOT 依赖客户端 Mermaid 运行时渲染图表。

#### Scenario: 构建生成图表

- **WHEN** 执行站点构建
- **THEN** `diagrams/` 中每个 `.mmd` 都被转为 SVG 并被页面正确引用

#### Scenario: 禁用 JavaScript 访问页面

- **WHEN** 浏览器禁用 JavaScript 打开含图表的页面
- **THEN** 图表仍然完整可见

### Requirement: 生成产物不可手改

SVG 产物 SHALL 视为生成物（标注生成来源或加入忽略清单）。对 SVG 的手工修改 SHALL 在下一次构建时被覆盖，MUST NOT 存在"只在 SVG 上改、源文件没改"的偏离状态。

#### Scenario: 手改 SVG 后重新构建

- **WHEN** 有人直接编辑生成出的 SVG 文件后重新构建
- **THEN** 该修改丢失，图表回到由 `.mmd` 源生成的状态

### Requirement: 图表内容脱敏

所有图表 SHALL 为对外重绘版本，MUST NOT 包含内部系统名、内部域名与端口、真实文件路径、真实人员标识；MUST NOT 使用内部系统截图。

#### Scenario: 参考内部文档中的现成图

- **WHEN** 写作时参考了内部文档里已有的架构图
- **THEN** 按原意重绘为脱敏版本（泛化命名、去内部路径），不复制原图

#### Scenario: 图中出现内部代号

- **WHEN** 某图表源码包含内部系统代号
- **THEN** 脱敏扫描在构建前拦下并中止构建
