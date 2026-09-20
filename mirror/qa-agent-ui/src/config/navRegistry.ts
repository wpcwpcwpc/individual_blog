import type { LucideIcon } from 'lucide-react'
import { PlusCircle, Activity, Cpu, Plug, Hammer } from 'lucide-react'

export interface NavItem {
  id: string
  label: string
  icon: LucideIcon
  type: 'action' | 'navigate' | 'link'
  route?: string               // navigate 型必填
  url?: string                 // link 型必填（外部链接，新开页签打开）
  onActivate?: () => void       // action 型用回调
  group?: 'more'                    // 声明该项归入「更多」折叠组,由 Sidebar 折叠渲染
}

/**
 * Sidebar 顶部功能区 nav 项注册表。
 * 后续新增垂类功能只需在此注册一行，Sidebar 零改动。
 * 挂 group:'more' 的项收进「更多」折叠组,点击「更多」展开显示。
 */
export const navItems: NavItem[] = [
  {
    id: 'new-chat',
    label: '新对话',
    icon: PlusCircle,
    type: 'action',
    // onActivate 在 Sidebar 组件内绑定（需访问 CreateSessionModal state / navigate）
  },
  {
    id: 'mcp',
    label: 'MCP 管理',
    icon: Plug,
    type: 'navigate',
    route: '/mcp',
    group: 'more',
  },
  {
    id: 'coding',
    label: '编码工作台',
    icon: Hammer,
    type: 'navigate',
    route: '/coding',
  },
  {
    id: 'tracing',
    label: '会话追踪',
    icon: Activity,
    type: 'navigate',
    route: '/tracing',
    group: 'more',
  },
  {
    id: 'system',
    label: '系统状态',
    icon: Cpu,
    type: 'navigate',
    route: '/system',
    group: 'more',
  },
]