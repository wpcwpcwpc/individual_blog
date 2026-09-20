## ADDED Requirements

### Requirement: 方案与选型审批

Agent SHALL 提供一个阻塞审批工具，接受 `kind`、`title`、`content_md` 三个参数，`kind` MUST ∈ {`plan`, `design`}；非平凡改动 MUST 在动手前取得批准。

#### Scenario: 提交方案

- **WHEN** Agent 调 `request_approval(kind="plan", title=..., content_md=<方案全文>)`
- **THEN** run 暂停，审批卡展示标题与方案全文，用户批准后返回确认文本并继续

#### Scenario: 载荷不完整

- **WHEN** `title` 或 `content_md` 为空
- **THEN** 工具拒绝执行并返回明确错误，MUST NOT 产生空审批卡

#### Scenario: 非法 kind

- **WHEN** `kind` 不在允许集合内（含已废除的业务 kind）
- **THEN** 工具拒绝执行并返回允许集合

### Requirement: 写盘闸门

Agent SHALL 仅能通过两个阻塞审批工具改变文件内容：整文件写入与精确替换；两者 MUST 各带 `purpose`（本次改动要达成什么）并展示在审批卡上。

#### Scenario: 新建或覆盖文件

- **WHEN** Agent 调 `write_file(path, content, purpose)`
- **THEN** run 暂停等待批准；批准后落盘；内容为 Python 源码时 SHALL 在落盘前做语法校验，语法错误 MUST 阻断落盘

#### Scenario: 精确替换

- **WHEN** Agent 调 `edit_file(file_path, old_string, new_string, replace_all, purpose)`
- **THEN** run 暂停等待批准；批准后执行替换，并保留既有校验链（改前必读、外部改动过期检测、多重匹配拒绝）

#### Scenario: 未读过文件就改

- **WHEN** Agent 在未读取目标文件的情况下提交替换
- **THEN** 校验链返回「先读再改」的指引，MUST NOT 落盘

#### Scenario: 缺少 purpose

- **WHEN** `purpose` 为空
- **THEN** 工具拒绝执行（审批卡信息充分性）

### Requirement: 危险命令双层拦截

系统 SHALL 在工具调用层拦截不可逆命令，并提供唯一的审批通道执行这类命令。

#### Scenario: 危险命令走安全通道

- **WHEN** Agent 用通用命令工具提交命中不可逆模式（递归删除、盘符格式化、强推、硬重置、清空工作树、整表删除、发布、递归改权限等）的命令
- **THEN** 调用被拦截、命令不执行，返回改用审批通道的指引

#### Scenario: 审批通道执行

- **WHEN** Agent 改调 `run_command(command, purpose)`
- **THEN** run 暂停，审批卡展示命令全文与目的；批准后执行，拒绝后不执行

#### Scenario: 安全命令直通

- **WHEN** Agent 提交未命中危险模式的命令
- **THEN** 经通用命令工具直接执行，不弹审批卡

#### Scenario: 缺少 purpose

- **WHEN** `run_command` 的 `purpose` 为空
- **THEN** 工具拒绝执行

### Requirement: 澄清门纪律

Agent SHALL 使用框架内置的结构化人机问答工具收集缺失信息；澄清调用 MUST 独占一轮，且 MUST NOT 伴随任何写盘、命令执行或审批调用。

#### Scenario: 需求缺关键信息

- **WHEN** 任务缺少决策点（可选方案收敛）或具体取值（名称、数量、自由描述）
- **THEN** Agent 调结构化问答工具暂停等用户作答，一轮内不带任何副作用调用

#### Scenario: 澄清门零副作用

- **WHEN** 用户作答后会话继续
- **THEN** 澄清过程本身未产生任何文件改动、命令执行或审批记录
