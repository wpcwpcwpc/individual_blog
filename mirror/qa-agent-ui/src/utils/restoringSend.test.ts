/// <reference types="vitest/globals" />
import { enqueueRestoringSend, resetRestoringPromisesForTest, type ResendPayload } from '@/utils/restoringSend'

// ── API mocks（可阻塞 restore，验证去重挂接语义）─────────────────

const { calls, state } = vi.hoisted(() => ({
  calls: [] as string[],
  state: { restoreBlocks: false, restoreRejects: false, releaseRestore: null as null | (() => void) },
}))

vi.mock('@/api/sessions', () => ({
  restoreSession: vi.fn(async () => {
    calls.push('restore')
    if (state.restoreRejects) throw new Error('restore boom')
    if (state.restoreBlocks) {
      await new Promise<void>(resolve => {
        state.releaseRestore = resolve
      })
    }
  }),
  sendMessage: vi.fn(async (_sid: string, payload: { content: string }) => {
    calls.push(`send:${payload.content}`)
  }),
}))

function makePayload(content: string): ResendPayload {
  return { content, stream: true }
}

beforeEach(() => {
  calls.length = 0
  state.restoreBlocks = false
  state.restoreRejects = false
  state.releaseRestore = null
  resetRestoringPromisesForTest()
})

// ── 去重语义（2.2）───────────────────────────────────────────────

describe('enqueueRestoringSend — per-session dedup', () => {
  it('second send during restore attaches: waits for restore, sends its own payload', async () => {
    state.restoreBlocks = true

    const p1 = enqueueRestoringSend('sid', 'user@test', makePayload('first'))
    await Promise.resolve() // 让 restore 进入阻塞态
    expect(calls).toEqual(['restore'])

    const p2 = enqueueRestoringSend('sid', 'user@test', makePayload('second'))
    await Promise.resolve()
    // 不触发第二次 restore —— 挂接到进行中的链
    expect(calls).toEqual(['restore'])

    state.releaseRestore?.()
    await Promise.all([p1, p2])

    expect(calls).toEqual(['restore', 'send:first', 'send:second'])
  })

  it('starts a fresh restore after the previous chain completes', async () => {
    await enqueueRestoringSend('sid', 'user@test', makePayload('first'))
    expect(calls).toEqual(['restore', 'send:first'])

    await enqueueRestoringSend('sid', 'user@test', makePayload('second'))
    expect(calls).toEqual(['restore', 'send:first', 'restore', 'send:second'])
  })

  it('restore failure clears the chain so the next send can retry', async () => {
    state.restoreRejects = true
    const p1 = enqueueRestoringSend('sid', 'user@test', makePayload('first'))
    await expect(p1).rejects.toThrow('restore boom')
    expect(calls).toEqual(['restore'])

    state.restoreRejects = false // 下一次 restore 正常走通
    await enqueueRestoringSend('sid', 'user@test', makePayload('second'))
    expect(calls).toEqual(['restore', 'restore', 'send:second'])
  })
})
