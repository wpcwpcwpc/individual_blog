---
title: 把单 Agent 升级成 Team：能力映射与 fail-closed
summary: leader 不是某个 Agent 的实例，而是一次显式的能力映射；映射不了的，宁可不建。映射、适配、校验三步走，把发现时间从「上线后某天」提前到「创建那一刻」。
publishedAt: 2026-11-03
tags:
  - 多 Agent
  - 架构取舍
  - 工程化
---

> 这是设计复盘，不是规范。行为要求见 `openspec/specs/team-session-lifecycle`；编码约束见 `.codemaker/rules/`。行号截至 2026-11。

**一句话结论**：leader 不是某个 Agent 的实例，而是一次显式的能力映射；映射不了的，宁可不建。

## 场景

我们已经有了一批成熟的单 Agent：各自带模型槽、指令、工具集、hooks，以及上传、记录、通知这些宿主行为。多智能体的需求很自然——一个 leader 保留全部业务能力，把可并行的只读调查派给成员，自己基于结果做决策。

框架的现实先泼了冷水。Agno 的 `Team` 构造函数里没有 `leader_agent` 参数：你把 `Agent` 实例传进 `members`，它就只能当成员；leader 的行为完全由 Team 自己的 model、instructions、tools、hooks 决定。换句话说，**框架里 leader 不是一个对象，是一组配置**。

这逼出一个必须回答的问题：已有的 Agent 资产，怎么变成 leader？

## 我们的方案

答案是把「变成」拆成三个显式步骤：映射、适配、校验。

**第一步，逐项映射。** `build_leader_spec`（`qa-agent/coordinator/team_leader.py:133`）把 `AgentDefinition` 投影成 `LeaderCapabilitySpec`：模型槽、指令、工具名清单（`tool_names` 加 `extra_tools`，静态封闭集合，刻意不注入动态 MCP 和 Skill 工具）、执行策略与成员并发上限、成员角色。映射是纯函数——输入定义、输出 spec，不碰运行时，可以单独测试。

**第二步，hook 适配。** Team 的 hook 签名和 Agent 不一样，直接搬过去会在运行期拿到错误的参数。`team_leader_hooks.py` 里维护一张适配白名单（`ADAPTABLE_*`，`:30-37`）：每类 hook 写一个显式适配器，白名单外的 hook 不搬。

**第三步，创建期校验，fail-closed。** `check_leader_compatibility`（`team_leader.py:206`）返回不兼容项元组：未注册的工具、无法静态门禁的动态工具、没进适配白名单的 hook。创建入口（`api/server.py:854-880` 的 team 分支）只要看到任何一条，直接返回 422，Team 根本不会被构造。422 的响应体就是修复清单——哪一项、为什么、怎么改。

还有一块绕不开的胶水：`agent=None`。Team 运行工具时，SDK 注入的 agent 上下文是空的，工具内部的相对路径解析、权限检查、session_state 读取全部失效。我们的做法是造一个视图对象 `TeamLeaderAgentView`（`team_leader_context.py:47`），携带 `name`、`workspace_root`、`permission_mode`，用 `bind_leader_tool`（`:91`）把每个工具包一层闭包注入视图，再用 `make_leader_safety_hook`（`:115`）让安全 hook 以 leader 的身份被调用。其中 `LeaderSessionState`（`:27`）是个 dict 子类，同时支持属性读取和字典读取——因为工具链里两套读取约定并存，我们选择让兼容对象两边都伺候，而不是改整条工具链。

## 为什么这样选

**弃「复用 Agent 实例」**：框架不支持，硬塞进 `members` 它就变成成员。这条没有讨论空间，但它决定了后面所有设计——既然 leader 必须是「构造出来的」，那就把构造过程做成可审计的映射，而不是藏在某处的运行时魔法。

**弃「提示词模仿」**：把原 agent 的系统提示词拷给 Team 当指令，听起来零成本。实际会行为漂移——上传、记录、通知这些能力靠 hooks 和工具的组合实现，提示词模仿不出行为，只会模仿出口吻。你会得到一个「说话像原 agent、办事不像」的 leader，而这种偏差要等线上跑起来才会被发现。

**弃「在每个 Agent 上加委派工具」**：让任何 agent 都能带委派能力，意味着任何 agent 都可能变成事实上的 leader，权限面失控；而且单 Agent 模式和 Team 模式下同名工具的语义不同（是否过门禁、事件归属给谁），复用只会把两种语义搅在一起。

**弃「静默降级」**：hook 不适配就跳过、工具不认识就不带、上下文拿不到就传 None——每一条被跳过的能力都是运行期才暴露的洞。fail-closed 把发现时间从「上线后某天」提前到「创建那一刻」，代价只是接入时多填一次修复清单。

还有一个意外收益：**映射即文档**。`LeaderCapabilitySpec` 把「这个 leader 到底有什么能力」从散落在系统提示词与代码各处的事实，收敛成一份结构化清单——哪些工具、哪些 hook 适配过、执行策略与并发上限是什么。接入评审时看 spec 就能拍板，不用通读实现；出了越权问题，第一嫌疑人是映射表里多出来的一项，排查面从整个系统缩到一个纯函数。我们后来几次给 leader 增减能力，都是改映射、跑校验、重建会话三步，没有再碰过框架胶水层。

## 代价与边界

这套设计支付的代价，如实列：

- **接入摩擦**。新 hook 类型要先写适配器进白名单；agent 想当 leader 要过兼容校验。我们把接入速度换成了安全，这个交换在测试平台场景是对的，在「快速试验」场景会嫌烦。
- **投影不是引用**。`LeaderCapabilitySpec` 是定义的快照，原 `AgentDefinition` 改了不会自动回写到已存在的 Team 会话，要重建。对长会话这是坑，我们靠「创建期校验 + 重建即校验」兜住。
- **兼容层技术债**。`LeaderSessionState` 双通道读取是救急设计，工具链统一读取约定后应当拆掉。
- **框架面收敛**。Agno 升级若改 Team 构造参数，映射层要跟着动。我们把所有框架接触面收敛在 `coordinator/` 几个文件里，升级时炸的面积是可控的。

## 可复用结论清单

1. 升级路径选「映射」还是「复用」，先看框架的抽象边界，再看愿望——框架把 leader 定义成配置，就不要硬拗成对象。
2. 创建期 fail-closed 优于运行期降级；把校验失败的报错写成修复清单，用户照着改就能过。
3. hook / 插件类能力必须有显式适配白名单；白名单外即拒绝，不做「尽力兼容」。
4. 框架传不进上下文的地方（`agent=None` 类问题），造自己的视图对象注入，不要 patch 框架内部。
5. 能力映射做成纯函数（definition → spec），映射逻辑可单测，回归成本趋近于零。
6. 兼容层可以救急，但要在代码里注明技术债和拆除条件，否则它会活成永久架构。
7. 把能力 spec 当成 review 单元：增减能力走「改映射、跑校验、重建会话」三步，评审看清单而不是读代码。

## 延伸

- 实现：`qa-agent/coordinator/team_session.py`、`team_leader.py`、`team_leader_context.py`、`team_leader_hooks.py`
- 规范：`openspec/specs/team-session-lifecycle/spec.md`
- 变更：`openspec/changes/archive/2026-10-20-add-agno-team-sessions`
- 关联踩坑：总册 K12（`bash` 绕过工具白名单）——安全 hook 必须以 leader 视图执行、空 agent 直通即越权，动机见 `docs/pitfalls/踩坑总册.md`
- 全系列目录见[导读](/posts/team-notes-00-reading-guide)；委派通道的取舍见[第 2 篇](/posts/team-notes-02-delegation-as-atomic-tool)。
