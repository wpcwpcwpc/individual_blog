# 流式前端的两条命门：水位与渲染调度

## 引子

后端每秒推几十条事件，前端做错两件事就全盘皆输：**水位记早了**，**调度用错了**。

## 一、现场

### 1. 后台标签页放一会儿，会话「死住」

用户切到别的标签页，回来发现内容停在几分钟前，刷新才恢复。

我们把 flush 调度写成了 `requestAnimationFrame`——这是前端节流的常见写法。问题是：**后台标签页的 rAF 会被浏览器完全暂停**。缓冲永远不排空，看起来就是会话不动了。

改成 `setTimeout(16)` 之后还有一个坑：后台标签页的 `setTimeout` 会被 clamp 到约 1 秒。于是又加了有界兜底：`FLUSH_MAX_EVENTS = 200`、`FLUSH_MAX_CHARS = 64K`，触顶立即 flush，防止隐藏页恢复时一次性灌入巨量事件。

### 2. 断线重连后，中间少一段

这条更隐蔽。原来在 `onmessage` 里收到事件就立刻写 `lastSeqId`：

```ts
ws.onmessage = (msg) => {
  const ev = JSON.parse(msg.data);
  connection.lastSeqId = ev.seq_id;  // 收到即写
  handleEvent(ev);
};
```

引入缓冲之后，这个水位就变成了「**已收到**」而不是「**已应用**」。断线重连时，前端拿着一个超前的水位去请求重放，服务端从那个位置之后发——中间那段还在缓冲里、没进 store 的事件，就永久丢了。

正确做法反直觉：水位只能在 flush **应用完成之后**更新；`onclose` 时把未 flush 的缓冲直接丢弃，交给重放覆盖。

### 3. 点了停止，卡片还在往外冒

`run_aborted` 已经处理了，时间线上成员卡还在增量。

前端这里的原因是：`appendEvent` 在终态分支把 seq 高水位重置为 0。之后到达的迟到事件 seq 一定 > 0，去重逻辑被「合法地」关掉了。

水位重置应该绑在 **run 边界**（`run_started`），而不是终态。终态代表「这个 run 结束了」，不代表「这条连接不会再有事件」。

### 4. 高频 token 下，主线程连续长任务

一段 10 秒的流式回答，用户滚动明显掉帧。

链路是这样的：每条 WS 消息独立一个任务 → `handleEvent` → `appendEvent` → 一次 `set`。而 `appendEvent` 每次要新建整张 `sessions` map、新建 `record`、`[...timeline]` 拷贝、再按 `event_type` 走一遍 O(n) 的 `timeline.map`。

React 的自动批处理只在**同一个任务内**生效。每条 WS 消息都是独立任务，所以每个事件都触发一次独立渲染。

订阅面同时放大：`SessionPage` 订阅整张 `sessions`，常驻侧栏也订阅整张 map，三个组件各自订阅整条 `timeline`。全仓没有 `React.memo`、没有 `useShallow`——虚拟化只减少了挂载数量，对「内容更新」毫无保护。

### 5. 快速导航的高亮，永远慢一轮

点「跳到第 5 轮」，高亮停在第 4 轮；滚到底部，高亮倒数第二轮。

原因是我们用 `virtualizer.getVirtualItems()[0].index` 当「当前可见首项」。这个数组包含 overscan（5 个）——**位于视口上方的预渲染项**。于是「首项」永远比真实可见项早 5 个 index；末轮项数小于 overscan 时，会被完全跳过。

正确基准只能走 DOM：在容器内取带 `data-index` 的节点，找第一个 `rect.bottom > rootRect.top` 的。

还有一个必须写进注释的约束：**不能用 `scrollTop` 和 `virtualItem.start` 比较**。`start` 不包含顶部 spacer 之前的内容高度，DOM 基准天然包含哨兵、重试条这些偏移。

### 6. 精修新会话：正在流式输出的内容反复消失

现象是当前气泡刷着刷着整段被清空，历史气泡也重来一遍，最后 run 结束才一次性全量渲染。

根因是三份 hydrate effect 的守卫语义不一致：`timeline 为空 && loadStatus 不在 loading/restoring/error` 就放行加载。而新会话在 Agno storage 里还没有 run，`read_session_tail` 直接短路返回空，于是 `hydrateTailPage` 把 timeline 整体替换为空，又把状态置回 `ready`，条件再次成立——循环。

放大因素是 effect 的依赖写了 `sessionRecord` **整个对象引用**：store 每次变更都换引用（包括 30 秒心跳更新 `lastSeqId`），effect 就不断重跑。用户发送后，乐观消息卡加上实时累积，被在途请求的整替结果无条件抹掉，再拉一次。

### 7. 同一个组件，在两种宿主里长得不一样

悬停提示在一个宿主里是浅黄色，在另一个宿主里是纯黑。

因为提示用的是原生 `title`。它的样式由**宿主**渲染，页面 CSS 管不到：Chromium 给 `#ffffe1`；桌面客户端用 `QWebEngineView`，走 Qt 的 `QToolTip`，深色主题下是 `#2a2d3e`。

同族的还有 `createPortal(..., document.body)`：它为了避开虚拟列表的 `transform` 对 `fixed` 的劫持，把节点挂到了 body，同时也挂出了 `.theme-session` / `.theme-card` 的作用域。根类名还在，但 CSS 变量只能从 `:root` 解析，模块色调完全失效。

结论一句话：**类名在，不等于作用域在。**

## 二、我们当时怎么想的

- **「rAF 是标准节流手段。」** 它在可见标签页里确实是；后台标签页里它根本不执行。
- **「收到就写水位更实时。」** 水位不是「进度展示」，是「重放起点」。写早了等于宣布「这些事件我处理过了」。
- **「终态了就结束了。」** 后端可能还有队列残余、watcher 残余；连接的关闭与 run 的结束是两件事。
- **「类名一样，样式就一样。」** 原生控件样式、portal 节点、多宿主渲染，都不看你的类名。

## 三、真正的根因

1. **浏览器节流规则是分环境的。** rAF（不可见暂停）、setTimeout（后台 clamp 约 1s）、WebSocket onmessage（不受可见性影响，但受主线程卡顿影响）——三者语义完全不同。
2. **水位有两种语义。** 「已收到」用于网络层去重，「已应用」用于重放起点。混用就丢数据。
3. **React 批处理的边界是任务。** 事件源每帧一个任务时，批处理等于不存在；必须在进入 store 之前合并。
4. **虚拟列表的 index 是渲染窗口，不是可见窗口。** overscan 让两者永远差几个。
5. **portal 与原生控件逃出 CSS 作用域。** 作用域靠祖先链解析变量，不靠类名文本。

## 四、代价

- 后台标签页会话看起来死住，用户只能刷新；刷新又丢未落库的状态。
- 重连丢中间事件，时间线缺段且不可恢复。
- 高频 token 下主线程长任务堆积，滚动卡顿、输入延迟。
- 导航高亮错位，用户以为跳错了位置。
- 跨宿主样式不一致，同一份代码两种观感，排查成本高。

## 五、下次怎么提前发现

1. **调度器先问「不可见时还跑不跑」。** 需要后台继续工作的用 `setTimeout` + 有界缓冲；只有纯视觉更新才用 rAF。
2. **水位只记已应用，终态与连接关闭分开。** 代码里把 `lastSeqId` 改成 `appliedSeqId`，命名本身就能防错；水位重置只在 `run_started`，终态到达后仍要保留迟到事件的清理逻辑。
3. **事件入口先合并再入 store。** 合并谓词只允许「相邻、同类型、同轨道」，控制事件当屏障，不跨工具/成员/run 边界折叠。
4. **虚拟列表位置判断用 DOM 矩形。** 任何基于 `virtualItems[0].index` 的「当前可见」判断都要重写；也不要拿 `scrollTop` 与 `start` 比。
5. **portal 与原生控件单独验收。** portal 根节点重挂主题作用域；用户可见提示不要用原生 `title`，自绘或用统一组件。

## 延伸

- `openspec/changes/fix-timeline-render-backlog/design.md` —— rAF 暂停、水位语义、批量折叠
- `openspec/changes/fix-team-abort-backlog/design.md` —— 终态后的残余事件与水位重置
- `openspec/changes/fix-refine-live-render-chaos/design.md` —— hydrate 守卫与在途请求覆盖
- `openspec/changes/fix-turn-nav-window-model/design.md`、`harden-turn-quick-nav-ui/design.md` —— overscan 与滚动基准
- `openspec/changes/add-app-theme-system/design.md` —— portal 作用域、原生 `title`、多宿主样式
- `docs/pitfalls/踩坑总册.md` K6/K14/K15/K18 —— 事件水位与历史 hydrate 类问题
