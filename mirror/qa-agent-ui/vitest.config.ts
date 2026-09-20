import { defineConfig } from 'vitest/config'
import { fileURLToPath } from 'node:url'

// globals: true — Node 24.12 下 `import { describe } from 'vitest'` 的模块拦截
// 拿到未初始化实例（"Cannot read properties of undefined (reading 'config')"），
// 全局注入模式绕开该链路。
// 测试文件顶部用 `/// <reference types="vitest/globals" />` 提供 TS 类型。
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    globals: true,
  },
})
