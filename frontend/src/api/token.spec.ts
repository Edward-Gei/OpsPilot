// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

vi.mock('@/api/auth', () => ({ fetchMe: vi.fn(), logout: vi.fn() }))

class TestChannel {
  static channels: TestChannel[] = []
  static holdUpdates = false
  static updates: (() => void)[] = []
  onmessage: ((event: MessageEvent) => void) | null = null

  constructor(readonly name: string) {
    TestChannel.channels.push(this)
  }

  postMessage(data: { type: string }) {
    for (const peer of TestChannel.channels) {
      if (peer === this || peer.name !== this.name) continue
      const deliver = () => peer.onmessage?.({ data } as MessageEvent)
      if (TestChannel.holdUpdates && data.type === 'tokens') TestChannel.updates.push(deliver)
      else queueMicrotask(deliver)
    }
  }

  static flushUpdates() {
    for (const deliver of TestChannel.updates.splice(0)) deliver()
  }
}

async function openTab() {
  const values = new Map<string, string>()
  vi.stubGlobal('sessionStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  })
  vi.resetModules()
  return { tokens: await import('./token'), values }
}

function refreshJwt(claims: Record<string, number>) {
  return `header.${btoa(JSON.stringify(claims))}.signature`
}

beforeEach(() => {
  TestChannel.channels = []
  TestChannel.updates = []
  TestChannel.holdUpdates = false
  vi.stubGlobal('BroadcastChannel', TestChannel)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('新窗口从仍打开的已登录窗口取得会话，令牌只存在各自 sessionStorage', async () => {
  const first = await openTab()
  first.tokens.setTokens('access-1', 'refresh-1')
  const second = await openTab()

  expect(second.tokens.getAccessToken()).toBe('')
  expect(await second.tokens.restoreTokensFromPeer()).toBe(true)
  expect(second.tokens.getAccessToken()).toBe('access-1')
  expect(second.tokens.getRefreshToken()).toBe('refresh-1')
  expect(first.values.get('opspilot_access_token')).toBe('access-1')
  expect(second.values.get('opspilot_access_token')).toBe('access-1')
})

it('没有已登录窗口时，等待结束后仍保持未登录', async () => {
  vi.useFakeTimers()
  const tab = await openTab()
  const pending = tab.tokens.restoreTokensFromPeer(50)
  await vi.advanceTimersByTimeAsync(50)
  expect(await pending).toBe(false)
  expect(tab.tokens.getAccessToken()).toBe('')
})

it('同一会话的令牌旋转会同步，独立登录的窗口不会被覆盖', async () => {
  const first = await openTab()
  first.tokens.setTokens('access-1', 'refresh-1')
  const second = await openTab()
  await second.tokens.restoreTokensFromPeer()
  const unrelated = await openTab()
  unrelated.tokens.setTokens('other-access', 'other-refresh')

  first.tokens.setTokens('access-2', 'refresh-2')
  await Promise.resolve()
  expect(second.tokens.getRefreshToken()).toBe('refresh-2')
  expect(unrelated.tokens.getRefreshToken()).toBe('other-refresh')
})

it('已登录窗口重新登录另一账号时结束旧共享会话', async () => {
  const first = await openTab()
  const { createPinia, setActivePinia } = await import('pinia')
  const { useUserStore } = await import('@/stores/user')
  setActivePinia(createPinia())
  const user = useUserStore()
  user.applyTokens({ access_token: 'access-1', refresh_token: 'refresh-1', token_type: 'bearer', expires_in: 900 })
  const second = await openTab()
  await second.tokens.restoreTokensFromPeer()

  user.applyTokens({ access_token: 'other-access', refresh_token: 'other-refresh', token_type: 'bearer', expires_in: 900 })
  await Promise.resolve()
  expect(first.tokens.getAccessToken()).toBe('other-access')
  expect(second.tokens.getAccessToken()).toBe('')
})

it('登出同步清除共享会话，延迟到达的旧轮换消息不能恢复登录', async () => {
  const first = await openTab()
  first.tokens.setTokens('access-1', 'refresh-1')
  const second = await openTab()
  await second.tokens.restoreTokensFromPeer()

  TestChannel.holdUpdates = true
  first.tokens.setTokens('access-2', 'refresh-2')
  first.tokens.clearTokens()
  await Promise.resolve()
  expect(second.tokens.getAccessToken()).toBe('')

  TestChannel.flushUpdates()
  expect(second.tokens.getAccessToken()).toBe('')
  expect(second.tokens.getRefreshToken()).toBe('')
})

it('单个窗口刷新失败时可以只清本地令牌，保留其他窗口会话', async () => {
  const first = await openTab()
  first.tokens.setTokens('access-1', 'refresh-1')
  const second = await openTab()
  await second.tokens.restoreTokensFromPeer()

  first.tokens.clearTokens(false)
  await Promise.resolve()
  expect(first.tokens.getAccessToken()).toBe('')
  expect(second.tokens.getAccessToken()).toBe('access-1')
})

it('固定会话截止到点后清除所有共享窗口，续期不延长截止时间', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-26T06:00:00Z'))
  const deadline = Math.floor(Date.now() / 1000) + 60
  const first = await openTab()
  first.tokens.setTokens('access-1', refreshJwt({ session_exp: deadline, exp: deadline + 3600 }))
  const second = await openTab()
  await second.tokens.restoreTokensFromPeer()

  await vi.advanceTimersByTimeAsync(30_000)
  first.tokens.setTokens('access-2', refreshJwt({ session_exp: deadline, exp: deadline + 7200 }))
  await Promise.resolve()
  await vi.advanceTimersByTimeAsync(30_000)
  expect(first.tokens.getAccessToken()).toBe('')
  expect(second.tokens.getAccessToken()).toBe('')
})

it('旧令牌按 exp 截止，后台节流后聚焦时补做过期检查', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-26T06:00:00Z'))
  const deadline = Math.floor(Date.now() / 1000) + 60
  const tab = await openTab()
  tab.tokens.setTokens('access', refreshJwt({ exp: deadline }))

  vi.setSystemTime(new Date((deadline + 1) * 1000))
  window.dispatchEvent(new Event('focus'))
  expect(tab.tokens.getAccessToken()).toBe('')
})

it('已过期的后台窗口不会向新窗口交出令牌', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-26T06:00:00Z'))
  const first = await openTab()
  first.tokens.setTokens('access', refreshJwt({ session_exp: Math.floor(Date.now() / 1000) + 60 }))
  vi.setSystemTime(new Date('2026-10-26T06:01:01Z'))
  const second = await openTab()
  const pending = second.tokens.restoreTokensFromPeer(50)
  await vi.advanceTimersByTimeAsync(50)

  expect(await pending).toBe(false)
  expect(first.tokens.getAccessToken()).toBe('')
  expect(second.tokens.getAccessToken()).toBe('')
})

it('其他窗口登出后清除当前窗口的内存用户信息', async () => {
  const first = await openTab()
  first.tokens.setTokens('access', 'refresh')
  const second = await openTab()
  await second.tokens.restoreTokensFromPeer()
  const { createPinia, setActivePinia } = await import('pinia')
  const { useUserStore } = await import('@/stores/user')
  const { fetchMe } = await import('@/api/auth')
  vi.mocked(fetchMe).mockResolvedValueOnce({ id: 1, username: 'alice', display_name: 'Alice', email: null,
    source: 'local', mfa_enabled: false, last_login_at: null, roles: [], permissions: ['cmdb:read'] })
  setActivePinia(createPinia())
  const user = useUserStore()
  await user.loadUserInfo()
  expect(user.userInfo?.username).toBe('alice')

  first.tokens.clearTokens()
  await Promise.resolve()
  expect(user.userInfo).toBeNull()
})
