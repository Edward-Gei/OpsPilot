// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  access: '', refresh: '', sessionId: 'session-1', expired: false, clearModes: [] as (boolean | undefined)[],
  onResponseError: null as null | ((error: unknown) => Promise<unknown>),
  post: vi.fn(), request: vi.fn(), waitForTokenChange: vi.fn(),
}))

vi.mock('axios', () => ({ default: {
  create: () => ({
    interceptors: { request: { use: () => {} }, response: { use: (_ok: unknown, fail: typeof state.onResponseError) => { state.onResponseError = fail } } },
    request: state.request,
  }),
  post: state.post,
  isCancel: () => false,
} }))
vi.mock('ant-design-vue', () => ({ message: { error: vi.fn() } }))
vi.mock('./token', () => ({
  getAccessToken: () => state.access,
  getRefreshToken: () => state.refresh,
  getSessionId: () => state.access && state.refresh ? state.sessionId : '',
  setTokens: (access: string, refresh: string) => { state.access = access; state.refresh = refresh },
  clearTokens: (broadcast?: boolean) => { state.clearModes.push(broadcast); state.access = ''; state.refresh = '' },
  waitForTokenChange: state.waitForTokenChange,
  expireIfNeeded: () => state.expired,
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

function expiredRequest() {
  return state.onResponseError!({ response: { data: { code: 40102 } }, config: { url: '/cmdb/apps', headers: {} } })
}

beforeEach(async () => {
  state.access = 'access-1'
  state.refresh = 'refresh-1'
  state.sessionId = 'session-1'
  state.expired = false
  state.clearModes = []
  state.post.mockReset()
  state.request.mockReset().mockResolvedValue({ data: { code: 0, data: 'ok' } })
  state.waitForTokenChange.mockReset().mockImplementation(async (sessionId: string, refresh: string) =>
    state.sessionId === sessionId && !!state.access && state.refresh !== refresh)
  vi.stubGlobal('window', { location: { pathname: '/login', href: '/login' } })
  vi.resetModules()
  await import('./http')
})

afterEach(() => { vi.unstubAllGlobals() })

it('另一窗口已旋转令牌时，旧 Refresh 失败后使用新令牌重放请求', async () => {
  const refresh = deferred<unknown>()
  state.post.mockReturnValue(refresh.promise)
  const result = expiredRequest()
  await Promise.resolve()

  state.access = 'access-2'
  state.refresh = 'refresh-2'
  refresh.reject(new Error('旧 Refresh 已吊销'))

  await expect(result).resolves.toEqual({ data: { code: 0, data: 'ok' } })
  expect(state.refresh).toBe('refresh-2')
  expect(state.clearModes).toEqual([])
})

it('旧 Refresh 先失败、另一窗口随后同步新令牌时继续原请求', async () => {
  state.post.mockRejectedValue(new Error('旧 Refresh 已吊销'))
  state.waitForTokenChange.mockImplementation(async () => {
    state.access = 'access-2'
    state.refresh = 'refresh-2'
    return true
  })

  await expect(expiredRequest()).resolves.toEqual({ data: { code: 0, data: 'ok' } })
  expect(state.clearModes).toEqual([])
})

it('登出后迟到的刷新成功响应不能重新写入令牌', async () => {
  const refresh = deferred<unknown>()
  state.post.mockReturnValue(refresh.promise)
  const result = expiredRequest()
  await Promise.resolve()

  state.access = ''
  state.refresh = ''
  refresh.resolve({ data: { code: 0, data: { access_token: 'late-access', refresh_token: 'late-refresh' } } })

  await expect(result).rejects.toThrow()
  expect(state.refresh).toBe('')
  expect(state.request).not.toHaveBeenCalled()
})

it('固定会话截止后迟到的刷新成功响应不能恢复登录', async () => {
  const refresh = deferred<unknown>()
  state.post.mockReturnValue(refresh.promise)
  const result = expiredRequest()
  await Promise.resolve()

  state.expired = true
  refresh.resolve({ data: { code: 0, data: { access_token: 'late-access', refresh_token: 'late-refresh' } } })

  await expect(result).rejects.toThrow()
  expect(state.refresh).toBe('')
  expect(state.request).not.toHaveBeenCalled()
})

it('本窗口刷新失败且没有新令牌时只清本窗口，不广播登出其他窗口', async () => {
  state.post.mockRejectedValue(new Error('Refresh 已失效'))
  await expect(expiredRequest()).rejects.toThrow()
  expect(state.clearModes).toEqual([false])
})
