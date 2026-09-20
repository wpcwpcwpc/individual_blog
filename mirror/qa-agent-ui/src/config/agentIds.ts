/**
 * Agent id 常量（跨页面共享）。
 * 放独立模块而非懒加载页面文件，避免主 chunk（SessionList/SessionPage）反向 import 页面 chunk。
 */
export const CODING_AGENT_ID = 'coding-agent'
