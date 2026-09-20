/// <reference types="vitest/globals" />
import { getWSStreamUrl } from './sessions'

// jsdom 默认 origin: http://localhost:3000/ → 相对 base 应补全为 ws://localhost:3000
const PAGE_ORIGIN = 'ws://localhost:3000'

describe('getWSStreamUrl', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('build:web 烤入的相对 /api base 应补全为绝对 ws URL（桌面打包场景）', () => {
    // 与 vite.config.ts define 注入值一致（vitest 会加载 .env.local，需显式覆盖）
    vi.stubEnv('VITE_API_BASE_URL', '/api')
    expect(getWSStreamUrl('sid-1')).toBe(`${PAGE_ORIGIN}/api/sessions/sid-1/stream/ws`)
  })

  it('dev 模式 .env.local 注入的绝对 http base 应转换为 ws 直连', () => {
    vi.stubEnv('VITE_API_BASE_URL', 'http://127.0.0.1:8000/api')
    expect(getWSStreamUrl('sid-2')).toBe('ws://127.0.0.1:8000/api/sessions/sid-2/stream/ws')
  })

  it('https base 应转换为 wss', () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://qa.example.com/api')
    expect(getWSStreamUrl('sid-3')).toBe('wss://qa.example.com/api/sessions/sid-3/stream/ws')
  })

  it('lastEventId 应追加为查询参数', () => {
    vi.stubEnv('VITE_API_BASE_URL', '/api')
    expect(getWSStreamUrl('sid-4', 42)).toBe(
      `${PAGE_ORIGIN}/api/sessions/sid-4/stream/ws?last_event_id=42`,
    )
  })

  it('产物必须是合法绝对 ws(s) URL（WebSocket 构造器不接受相对路径）', () => {
    vi.stubEnv('VITE_API_BASE_URL', '/api')
    expect(getWSStreamUrl('sid-5')).toMatch(/^wss?:\/\//)
  })
})
