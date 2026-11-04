---
title: 可观测是契约不是插件：事件身份、token 归属与回放同构
summary: 成员一多，观测先崩；身份契约必须先于代码定下来，实时与回放必须消费同一份归一化事件——差异只允许存在于取数层，不允许存在于归一化层。
publishedAt: 2026-11-03
tags:
  - 多 Agent
  - 可观测性
  - 工程化
---

> 这是设计复盘，不是规范。行为要求见 `openspec/specs/team-observability`；编码约束见 `.codemaker/rules/`。行号截至 2026-11。

**一句话结论**：成员一多，观测先崩；身份契约必须先于代码定下来，实时与回放必须消费同一份归一化事件。

## 场景

单 Agent 的观测是「顺带就有了」：一条事件流，seq 排序，前端照画。加入成员后，观测以三种方式崩塌：

1. **事件对不上号**。成员事件混进总流：这条 token 属于哪个成员？这次工具调用和它的结果是同一对吗？重试之后同一成员出现两个 run，哪个是哪次？——时间线变成了雪花噪声。
2. **token 算不清**。成本统计按 `agent_name` 聚合：同名成员多次尝试合在一起，leader 与成员的分摊拍脑袋。更糟的是缺失值被兄弟 run「填充」，数字看起来完整，实际上不可信——而成本数字一旦不可信，就永远不可信了。
3. **回放像另一个系统**。实时 WS 是一条链路，历史回放是另一条。字段名不同、语义有出入、成员轨迹干脆不在。用户实时看到的和刷新后回放看到的对不上，每个差异都是一张工单。

三种崩塌的共同根因：观测被当成「功能做完后补的插件」，而不是「写代码前定的契约」。

## 我们的方案

**先定契约，再写代码。** 成员事件有一个最小身份契约：`session_id`、父级 `run_id`、稳定的 `member_id`、成员自己的 `run_id`、`event_type`；工具类事件再加 `tool_call_id`。五加一字段，每个事件必须带齐。落点在 `_member_lifecycle_event`（`core/stream_adapter.py:484`）——它是六个 `member_*` 事件（开始、进度、思考、工具调用、工具结果、结束）的**唯一出口**，字段在这里组装，别处只消费。成员事件经 `member_event_queue` 汇入合流双泵，与 leader 帧按 seq 交织成一条流；seq 跨 run 单调不重置，先 `event_store.append` 落库、再推 WS（`api/server.py:2687-2692`）——顺序反过来就会产生「推出去但没存下」的窗口。

**实时与回放同构。** 归一化只有一条链路：`_process_member_event`（`stream_adapter.py:601`）复用单 Agent 的事件分类逻辑，再叠加成员身份——token 归一成 `member_progress`，reasoning 归一成 `member_thinking`。实时推的是它，落库存的是它，回放读的还是它。前端拿到的语义永远一致，「刷新后像换了个系统」这类问题在结构上不存在。

**token 按 run 归属，缺失即未知。** 每个成员 run 独立记录自己的用量；某个 run 缺数据就显示未知，不用父级或兄弟填充。聚合永远可逆地做（从 run 往上汇总），绝不把不可逆的合并写进数据。

**trace 挂父树。** 成员协程启动时用 contextvars attach leader 当前 span（`_member_trace_context`，`team_delegate.py:34`），成员产生的 span 自然挂到 leader 的 trace 树上——一次调查在链路视图里是一棵树，不是几座孤岛。取不到 leader 上下文时退化成独立 root，不报错不阻塞；span 携带用户标识，缺失就缺失，同样不填充。

**出栈分两层。** 时间线刷新走合并投影——卡片级摘要，payload 有界，一次刷新不会拉爆；要看细节走 run 级详情端点（`api/member_run_detail.py:161`），工具结果默认 2000 字符截断并带 `result_truncated` 标记，全文顺着[第 4 篇](/posts/team-notes-04-member-least-privilege)的 `result_id` 去结果文件拿。终态会话回放时，`_merge_member_trace_messages`（`core/storage_reader.py:200`)按 `member_run_id` 把成员留痕 join 进历史，注入成员摘要消息——回放里成员不是幽灵。

## 为什么这样选

**弃「各链路自拼字段」**：每条事件链自己决定带什么字段，前期最快，代价是每个下游消费方都要为字段差异打补丁，且补丁本身成为新的漂移源。契约收敛在一个出口函数后，字段问题从「处处防」变成「一处审」。

**弃「按 `agent_name` 聚合 token」**：同名多 run、重试、并行尝试，聚合后的数字无法拆回。成本数据的价值在于可归因，一旦合并就不可逆——我们宁可短期看到「未知」块，也不生产看似完整的假数字。

**弃「trace 独立 root」**：实现最简单，但成员调查在 leader 的链路视图里消失，跨成员排障要人工对时间戳。挂父树后「哪次委派引入的慢查询」一眼可见，这个可观测收益直接对应排障时间的下降。

**弃「全量消息进投影」**：时间线一次刷新全量拉，payload 随成员数与轮数无界增长，前端先卡后崩。两层出栈（投影 + 详情）让「快」与「全」各走各的通道。

**弃「回放另写一条链路」**：回放的历史数据结构与实时不同（要 merge 留痕），但它消费的事件语义必须与实时相同。差异允许存在于**取数层**，不允许存在于**归一化层**。

## 代价与边界

- **契约演进是全链路成本**。新增一个事件字段，要从归一化出口、存储 schema、投影、前端类型四处显式声明——pydantic 会静默剥掉未声明字段，前端 TypeScript 会拒绝未知形状，任何一处漏了都是运行期才爆。这是契约的税，换来的是字段不可能悄悄漂移。
- **成员思考流有开关**。`member_thinking` 的量级等于每个成员一份 thinking，默认可关——观测的完整性要让位于「默认可用」。
- **详情截断**。超长工具结果在详情页只能看到前 2000 字符，看全文要去结果文件。与[第 4 篇](/posts/team-notes-04-member-least-privilege)的信封机制衔接，但意味着排障时要多跳一步。
- **trace 挂接依赖执行上下文**。contextvars 方案要求 attach 发生在正确的协程上下文里；成员实际跑在线程执行器上，attach 时机错了就退化成独立 root——静默降级不报错，靠 trace 树的形态自查。

## 可复用结论清单

1. 多 Agent 系统动手前先定事件身份契约：会话、父 run、成员、成员 run、事件类型，工具事件加 `tool_call_id`。
2. 归一化收敛到唯一出口函数——字段问题从处处防御变成一处审查。
3. 事件先持久化再推送；seq 跨 run 单调，回放与实时才有统一的排序基准。
4. 计量数据按最细粒度（run）归属，缺失显示缺失；不可逆的合并永远不要写进数据。
5. trace 挂父树，退化要静默（独立 root + 不报错），降级路径也要设计。
6. 实时与回放共享归一化层；差异只允许存在于取数层。
7. 出栈分两层：有界投影保「快」，详情端点保「全」，别让一个接口同时背两个职责。

## 延伸

- 实现：`qa-agent/core/stream_adapter.py`、`coordinator/team_delegate.py`、`api/member_run_detail.py`、`core/storage_reader.py`
- 规范：`openspec/specs/team-observability/spec.md`
- 变更：`openspec/changes/fix-team-member-observability`、`openspec/changes/add-subagent-panel`
- 关联笔记：[第 4 篇](/posts/team-notes-04-member-least-privilege)（结果信封——详情截断后的全文去路）
