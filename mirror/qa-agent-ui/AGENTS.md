# qa-agent-ui 前端撰写注意事项

## 悬停提示：禁用原生 `title=`，统一用 `<Tooltip>` 组件

**规则**：需要鼠标悬停提示时，MUST NOT 使用原生 HTML `title=` 属性（包括 button / a / span / i / svg / div[role=button] / img 等所有元素），MUST 包裹共享组件：

```tsx
import { Tooltip } from '@/components/ui/Tooltip'

<Tooltip tip="提示内容">
  <button>…</button>
</Tooltip>
```

**原因**：本应用在 WebView 中运行，系统暗色模式下原生 `title=` 提示会渲染成黑底黑字，完全不可读。此问题已多次复发——`fix-tooltip-boundary-truncation` change 曾用 codemod 把全仓 114 处 `data-tip` / 交互元素 `title=` 迁移到 `<Tooltip>`，但之后每个新模块开发（如编码工作台）都会重新写原生 `title=`，再次复发。**写新组件时直接用 `<Tooltip>`，不要复测这个坑。**

**要点**：

- `<Tooltip>`（Radix Portal）渲染到 `document.body`，不会被父容器 `overflow` 截断，靠近边缘自动翻转避让。
- 配色已硬编码 inline style（`#fff5d6` 底 / `#1a1a1a` 字 / `#d4a017` 边框），刻意**不走 CSS 变量**——WebView 暗色模式会把 `var()` 反解成暗色，再次黑底黑字。不要"优化"成主题变量（背景见 `src/index.css`「Tooltip 跨主题稳定」注释）。
- 条件提示直接传 `tip={cond ?? undefined}`：`tip` 为 `undefined`/`null`/`''` 时组件只渲染 children，不挂浮层。
- `Tooltip.Trigger` 是 `asChild`，children 必须是能接 ref 的单一元素（button/span 等天然满足），不额外加 DOM 层、不破坏 flex 布局。
- 全仓交互元素不残留原生 `title=`：浮层一律走 Tooltip 组件。
- 2026-09 已全仓清零（含已移除页面在内的 22 处一并迁移）。grep 校验法：搜 ` title=`，剩余结果应只有 React 组件 prop（`<ConfirmDialog title=…>` / `<InfoModal title=…>` 等），不得有 DOM 元素属性。
