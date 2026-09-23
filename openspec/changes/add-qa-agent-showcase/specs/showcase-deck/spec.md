## ADDED Requirements

### Requirement: deck 页序结构

概览 deck SHALL 为约 12 页，页序 SHALL 为：封面 → 问题背景 → 架构总览 → Agent 编排 → 工具与外部集成 → 管控与安全 → 上下文工程 → 评测体系 → 可观测 → 工程方法 → 结果与角色 → 联系方式。

#### Scenario: 3 分钟通读

- **WHEN** 读者以 3 分钟通读 deck
- **THEN** 能获得"问题 → 方案 → 关键机制 → 结果"的完整脉络

#### Scenario: 联系方式页

- **WHEN** 最后一页展示联系方式
- **THEN** 仅包含可公开渠道，MUST NOT 出现手机号或内部通讯账号

### Requirement: deck 与站点共用图源

deck 使用的图表 SHALL 取自站点图表管线产出的同一批 SVG。deck MUST NOT 单独维护一套图源，MUST NOT 出现与站点不一致的同意图图表。

#### Scenario: 修改某张架构图

- **WHEN** 图源 `.mmd` 被更新后重新构建
- **THEN** 站点文章与 deck 中的该图同步更新为新版本

#### Scenario: deck 需要站点没有的图

- **WHEN** deck 需要一张站点尚未有的图
- **THEN** 在图表管线中新增图源后两处共用，MUST NOT 在 deck 侧单独绘制

### Requirement: deck 排版沿用站点规范

deck 的排版 SHALL 满足与站点等效的约束：正文字号下限（手机可读）、每页要点 ≤5 条、代码块仅用于架构示意且 ≤15 行、一主色 + 灰阶双色系。

#### Scenario: 手机端查看 deck

- **WHEN** 用手机打开 deck 并逐页翻看
- **THEN** 文字无需放大即可阅读，无内容溢出屏幕

#### Scenario: 单页信息过载

- **WHEN** 某页要点超过 5 条
- **THEN** 该页拆分为两页或压缩表述

### Requirement: deck 与站点解耦

deck SHALL 以静态构建产物形式部署在站点的公开子目录，站点 SHALL NOT 依赖 deck 的构建工具链。deck 缺失或构建失败 MUST NOT 阻塞项目页与文章的构建与发布。

#### Scenario: deck 构建失败

- **WHEN** deck 构建因环境原因失败
- **THEN** 站点构建与发布仍正常完成，项目页上的 deck 入口显示为不可用或后续补齐状态

#### Scenario: 站点独立构建

- **WHEN** 只有站点源码、没有 deck 构建环境
- **THEN** 站点可以完整构建发布
