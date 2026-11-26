---
title: 一个会话只有一个运行：三层所有权与 HITL 续跑
summary: 运行所有权分三层——lease 管「谁在跑」，attempt 账本管「谁还没收工」，批次屏障管「事实是否齐了」；暂停不是结束，释放必须可证明。
publishedAt: 2026-11-03
tags:
  - 多 Agent
  - 架构取舍
  - 工程化
---

> 这是设计复盘，不是规范。行为要求见 `openspec/specs/team-session-lifecycle`；编码约束见 `.codemaker/rules/`。行号截至 2026-11。

**一句话结论**：运行所有权分三层——lease 管「谁在跑」，attempt 账本管「谁还没收工」，批次屏障管「事实是否齐了」；暂停不是结束，释放必须可证明。

## 场景

单 Agent 时代，「会话正在运行」是一把布尔锁。到了多 Agent，三个场景把布尔锁击穿了：

1. **并发轮**。leader 一轮要跑几分钟，用户等不及又发了一条消息——两个运行同时写同一份会话状态。
2. **假结束**。leader 调了个需要人审的工具，运行进入暂停等审批。如果把「暂停」当「结束」释放资源、关事件流，审批通过后就没地方续了。
3. **中断丢上下文**。用户中断一轮，紧接着发下一条——如果中断那一轮没落库，新轮次的模型读不到前文，从用户的视角是「AI 失忆了」。

三个场景指向同一个缺口：**运行是个有生命周期的对象，而系统里没有这个对象**。我们的答案是把它建模成三层所有权。

## 我们的方案

**第一层：lease，回答「这个会话此刻谁在跑」。** 每轮开始先 `begin_run`（`team_execution.py:120`）拿独占 lease，此时只有前端生成的临时 run_id，所以标记 provisional；收到框架首帧里的真实 run_id 后 `relabel_run`（`:144`）改名。会话上已有 open lease 时，新轮直接返回运行错误——不排队、不合并，明确告诉用户「忙」。释放时 `release_run`（`:169`）要求传入的 `team_run_id` 与持有者匹配，防止旧轮的清理代码误释放新轮。

lease 还有一个中间态：**draining**。释放时如果 attempt 账本里还有未终结的成员尝试（比如刚被中断，成员协程还在收尾），lease 不回到空闲，而是切成 draining，由排空观察器每 1 秒轮询一次（`_DRAIN_POLL_SECONDS=1.0`），600 秒超时后**依然保持 draining**——「未证明排空，就永远不视为排空」，期间新委派被阻止。

**第二层：attempt 账本，回答「还有谁没收工」。** 每个成员尝试一条账目，permit 的发放与回收和账本联动。它是 draining 判断的数据来源，也是「整轮到底还剩几个活口」的唯一权威。

**第三层：批次屏障，回答「事实是否齐了」。** [第 2 篇](/posts/team-notes-02-delegation-as-atomic-tool)讲过：`delegate_batch` 在全部成员到达真实终态后才返回。放在所有权的语境里，它是「运行可以安全结束」的证据来源。

**暂停 ≠ 结束，靠 gate 循环衔接。** 运行驱动（`team_run.py:407` 的 `stream_team_session_events`）发现状态为 paused 时进入循环（`:504`）：`gate.review` 审查暂停时挂起的整批工具调用 → 决议 → `gate.apply` 把 confirm/reject 写回 requirement → 发送 `persist_barrier` → `team.acontinue_run` 续跑 → 重新进入事件流。循环有预算：`_MAX_GATE_REJECT_ROUNDS = 16`（`:37`），只统计**连续整批被拒**的轮数，approve 轮不消耗预算——没有它，一个固执的 leader 可以和审查者永远拉锯。

**HITL 的权威在数据库，不在内存。** 暂停发生时框架把审批项写进 DB（按 `run_id` 一条，状态 pending/approved/rejected）。任何进程收到 `send_message`，入口先 `_detect_team_paused_run`（`api/server.py:2537`）：发现有 paused 运行且审批已决议 → `resolve_paused_requirements`（`team_hitl.py:73`）把决议翻译成 requirements，走 `_resume_paused_team_run`（`team_run.py:319`）以 `acontinue_run(run_id, requirements, input)` 续跑；审批还在 pending → 返回 409，告诉用户「有审批在等你」。续跑因此是跨进程、可重启的。

**中断的收口顺序是硬约束**：任务被取消 → 先做**有界落库**——`shield` + 最多等 3 秒，超时告警放行 → 写中断 checkpoint → **然后**才对外发 `run_aborted`。顺序反了就会出现「用户看到已中断、立刻发下一条、而上一轮还没落库」的窗口。成员侧的落库尽力而为，与批次补写共用同一把持久化锁，避免互相覆盖。

## 为什么这样选

**弃「单层互斥锁」**：锁只有持有与释放两个状态，表达不了「暂停但未结束」，更表达不了「正在排空」。而且锁活在进程内存里，重启即失忆——这正是后来 HITL 改用 DB approval 的同一理由。

**弃「内存 `asyncio.Event` 等审查」**：gate 循环等审查结果，最直觉的实现是挂在 Event 上。它对进程重启零抵抗，且把「等审批」这个可能以小时计的等待压进了事件循环。改成「暂停即终止本轮事件流 + DB 权威 + 显式续跑」，等待发生在轮次之间而不是协程里。

**弃「finally 里顺带落库」**：`CancelledError` 传播路径上，finally 里的 `await` 可能再次抛异常，把落库打断（这个坑我们在单 Agent 链路上已经吃过，见总册 K9）。所以中断落库必须 `shield` 加超时上限——宁可极端情况下丢最后一轮，不让中断收口被一次慢查询无限挂住。

**弃「中断 run 的同 run 续跑」**：中断后的运行不是框架意义上的可续状态，硬造续跑要与 lease、门禁、成员状态机三方纠缠。我们退了一步：只保证**新轮次上下文连续**（靠构造期常量 `add_history_to_context=True`，见[第 1 篇](/posts/team-notes-01-leader-capability-mapping)），并把这一点如实写进边界，不宣称断点续跑。

## 代价与边界

- **「忙」是产品语义**。lease 拒绝并发轮意味着用户在长运行中发消息会得到明确报错。要不要排队、要不要可打断重排，是产品决策，机制层不替产品做决定。
- **draining 最长占用 600 秒**。极端情况下会话资源挂 10 分钟不可复用。超时后保持 draining 而不是强放，是「宁可保守」的选择——强放省的是几分钟，赌上的是状态一致性。
- **3 秒落库上限**。远程 Mongo 抖到秒级以上时，中断恢复可能缺最后一轮内容。我们选择让中断收口有确定的上界，而不是让它的耗时不可预测。
- **状态机复杂度是真实成本**。三层所有权加 draining、加 gate 预算，状态转移图不开会讲不清楚。这是把布尔锁欠的债一次性还掉——债还了，图就复杂了。

## 可复用结论清单

1. 多 Agent 运行时需要一个显式的「运行」对象；布尔锁在出现暂停、中断、后台协程的那一刻就不够用了。
2. 临时 ID 与真实 ID 的交接（provisional → relabel）要成为显式步骤，否则清理逻辑会拿到错误的主键。
3. 「释放」必须可证明：还有未终结的尝试就保持 draining，阻止新委派，宁可保守。
4. 暂停不是结束：把「等审批」设计成轮次间的显式续跑，而不是协程内的等待。
5. HITL 权威放 DB，键用 run_id；任何进程都要能凭 DB 判断「这轮在等谁」。
6. 防拉锯要给循环加预算，且只计连续失败——正常路径不该消耗预算。
7. 中断收口有顺序契约：先有界落库，再通知外部；通知到达时，状态必须已可读。
8. 做不到的事写进边界（如同 run 续跑），比含糊地「基本支持」更省钱。

## 延伸

- 实现：`qa-agent/coordinator/team_execution.py`、`team_run.py`、`team_hitl.py`、`api/server.py`
- 规范：`openspec/specs/team-session-lifecycle/spec.md`
- 变更：`openspec/changes/fix-team-hitl-continuity`、`openspec/changes/fix-team-abort-resume-context`、`openspec/changes/fix-team-abort-backlog`
- 关联踩坑：总册 K9（`finally await` 中断持久化）、K26（响应丢失判定）——`docs/pitfalls/踩坑总册.md`
