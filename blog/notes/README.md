# 多 Agent 工程笔记 —— qa-agent Team 架构复盘

> 这是设计复盘，不是规范。行为要求见 `openspec/specs/team-session-lifecycle`、`team-member-execution`、`team-observability`；编码约束见 `.codemaker/rules/`。
>
> 实现行号截至 2026-11，漂移以函数名为准。

## 这套笔记讲什么

2026 年 10 月，我们把 qa-agent 从「单 Agent + 固定 workflow」升级成「Leader + Member 多智能体协作」：leader 保留宿主全部业务能力，把可并行的只读调查子任务委派给最小权限的成员，全部委派经过人审门禁，成员的每一步都可观测、可回放。

框架（Agno 2.x）给了 Team 这个原语，但没给工程。真正的工作量在框架之外：能力怎么映射、委派怎么审计、运行所有权怎么建模、成员权限怎么收、观测 schema 怎么定。这 5 篇笔记按架构维度还原这些决策——每个决策都写清楚「选了什么、弃了什么、付出什么代价」。

## 阅读顺序

| # | 文章 | 一句话钩子 | 适合谁读 |
|---|---|---|---|
| 01 | [把单 Agent 升级成 Team：能力映射与 fail-closed](01-leader-capability-mapping.md) | leader 不是某个 Agent 的实例，而是一次显式的能力映射；映射不了的，宁可不建 | 要在既有单 Agent 系统上加多智能体的人 |
| 02 | [委派是可审计的原子工具调用，不是框架魔法](02-delegation-as-atomic-tool.md) | 自编排 `delegate_batch`：批次屏障保证 leader 决策基于全部真实终态，原子门禁保证委派要么整批生效要么零副作用 | 要做任务编排、并行子任务的人 |
| 03 | [一个会话只有一个运行：三层所有权与 HITL 续跑](03-run-ownership-lease.md) | 暂停 ≠ 结束；「释放运行」必须是一个可证明的动作，而不是删一个标志位 | 踩过并发运行、中断丢上下文的人 |
| 04 | [成员是最小权限的：只读三件套与结果卸载](04-member-least-privilege.md) | 成员权限每多一项，治理成本翻一倍；大结果走指针不走值 | 要控制 Agent 权限面与上下文成本的人 |
| 05 | [可观测是契约不是插件：事件身份、token 归属与回放同构](05-observability-contract.md) | 成员一多，观测先崩；schema 必须先于代码定下来 | 要给多 Agent 系统补可观测的人 |

## 核心决策对照表

| 决策 | 我们的选择 | 被弃方案 | 弃因（一句话） | 详见 |
|---|---|---|---|---|
| leader 怎么构造 | 从 `AgentDefinition` 逐项映射 + 创建期 fail-closed | 复用 Agent 实例 / 提示词模仿 | 实例复用框架不支持；模仿会行为漂移 | 01 |
| `agent=None` 兼容 | 注入 `TeamLeaderAgentView` 视图对象 | 改 SDK / 放行空 agent | 改包不可持续；放行即越权 | 01 |
| 委派通道 | 自编排 `delegate_batch` 工具调用 | Team 原生 delegate | 拿不到真实终态、并发硬限、成员级 Stop、实时事件 | 02 |
| 工具门禁 | 全工具 `requires_confirmation` + gate 循环 | 提示词约束 / 拆分 Team run | 提示词不可靠；拆分丢 hooks 与运行记录连续性 | 02 |
| 运行所有权 | lease（整轮独占，含 draining）/ attempt 账本 / 批次屏障 | 单层互斥锁 | 「暂停≠结束」与「同步工具未排空」表达不了 | 03 |
| HITL 权威 | DB approval + `acontinue_run(requirements)` | 内存 `asyncio.Event` 等待 | 重启即丢，跨进程不可恢复 | 03 |
| 中断落库 | 先落库（shield 3s 上限）后发 `run_aborted` | 落库放 finally 里顺带做 | 终态可见后用户立刻发下一轮，必须已可读 | 03 |
| 成员权限 | 只读三件套（`file_read`/`grep`/`glob_search`） | 按任务动态开权 | 动态开权把安全边界变成运行时变量 | 04 |
| 成员会话 | 独立 `{sid}__member__{role}` | 与 leader 共享会话 | 共享则上下文/记忆/删除都无法隔离 | 04 |
| 大结果 | `result_id` 信封 + `read_result` 分窗回读 | 全量塞进 prompt / 存 Mongo | 前者爆上下文，后者重复持久化 | 04 |
| 回读保护 | `read_result` 限流 + 引导文案 | 抛异常 | 把控制权交回模型比报错更能自愈 | 04 |
| 事件身份 | 最小身份契约五字段 + `tool_call_id` | 各链路自拼字段 | 字段不一致时每个消费方都要打补丁 | 05 |
| token 归属 | 按成员 run 独立记录，缺失即未知 | 按 `agent_name` 合并 | 同名多 run 合并后数字不可信 | 05 |
| 实时与回放 | 同一归一化链路、同一份事件 | 各写一条链路 | 两条链路必然漂移，回放永远像「另一个系统」 | 05 |

## 边界声明

- 只覆盖已验收的 `coordinate` 模式；`route`/`broadcast`/`tasks` 在验收前被服务端明确拒绝。
- 不写踩坑细节——故障现象与根因在 `docs/pitfalls/踩坑总册.md`（K 编号），本系列只引用不复制。
- 不复述 spec 的规范性条款——延伸段给链接。
