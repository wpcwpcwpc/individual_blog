## ADDED Requirements

### Requirement: Agent 身份与注册

系统 SHALL 提供一个通用编码 Agent，名称为 `CodingAgent`，标识为 `coding-agent`，在 Agent 注册表中注册并可通过 `GET /agents` 枚举。

#### Scenario: 枚举注册表

- **WHEN** 查询 Agent 注册表
- **THEN** 结果含 `coding-agent`（名称 `CodingAgent`），且不含已删除的工坊 Agent

#### Scenario: 定义与装配分离

- **WHEN** 需要该 Agent 的运行实例
- **THEN** 注册表注册的是 builder 装配后的定义（含审批工具与守卫 hook，import 时装配），原始元数据 dataclass 不带运行时依赖，装配失败时降级为无审批能力的元数据定义并告警

### Requirement: 工具面构成

Agent SHALL 具备只读直通工具与阻塞审批工具两类能力；MUST NOT 具备目标仓库落盘白名单工具、游戏内代码执行工具、代码图谱检索工具、配置表检索工具、SVN 信息工具。

#### Scenario: 只读工具直通

- **WHEN** Agent 需要读文件、按内容检索、按路径检索、执行安全命令、等待或维护任务清单
- **THEN** 这些调用不需人工审批即可执行

#### Scenario: 副作用工具必审批

- **WHEN** Agent 需要写盘、执行破坏性命令或提交方案确认
- **THEN** 该调用 SHALL 走审批工具，run 暂停至用户决议

#### Scenario: 业务工具缺席

- **WHEN** 检查该 Agent 的工具清单
- **THEN** 不含工坊白名单写入、游戏内 REPL、代码图谱、配置表 RAG、SVN 信息等业务工具

### Requirement: 依赖切断

Agent SHALL NOT 依赖目标仓库布局、内部服务凭证与业务数据源；其 instructions MUST NOT 注入仓库规则文件正文、代码图谱可用性、业务 skill 指令。

#### Scenario: 无仓库上下文注入

- **WHEN** 构建该 Agent 的 instructions
- **THEN** 不含目标仓库根目录、规则文件正文、代码图谱可用性段落

#### Scenario: 不接业务外部能力

- **WHEN** 构建该 Agent
- **THEN** MCP 工具注入关闭、历史记忆注入关闭；skill 发现工具保留（仓库内既有 skill 仍可加载）

### Requirement: 行为契约

Agent instructions SHALL 约束以下行为：先探索后动手、禁止臆造 API 与路径、非平凡改动先过方案门、写盘只能经专用工具、改动前必读目标文件、命令按安全面分级、多步任务用任务清单推进、验证通过后才声明完成、遵循仓库既有风格且不引入新依赖、不落任何凭证。

#### Scenario: 声明完成的措辞

- **WHEN** Agent 报告任务完成但未实际运行验证
- **THEN** 契约要求其明确标注未验证，MUST NOT 以「应该可以」充当验证结论

#### Scenario: 无从确认的 API

- **WHEN** 生成代码需要某个符号而无法从仓库中确认真实形态
- **THEN** 契约要求留占位并说明缺口，MUST NOT 凭印象编造调用

### Requirement: 会话状态不依赖业务字段

Agent MUST NOT 读写工坊阶段状态字段（`case_phase` 等）；多步任务进度 SHALL 经任务清单工具承载。

#### Scenario: 会话状态使用

- **WHEN** 该 Agent 运行多轮任务
- **THEN** 阶段字段保持初始值不变，任务进度体现在任务清单
