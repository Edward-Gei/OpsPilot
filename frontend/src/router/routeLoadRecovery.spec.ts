// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { message } from 'ant-design-vue'
import { handleRouteError, isHandledRouteError } from './routeLoadRecovery'

vi.mock('ant-design-vue', () => ({ message: { error: vi.fn() } }))

const replace = vi.fn()
const fetchIndex = vi.fn()
const moduleError = () => new TypeError('Failed to fetch dynamically imported module: /assets/Dashboard-old.js')
const html = (src = '/assets/index-new.js') => `<html><head><script type="module" src="${src}"></script></head></html>`
let entry: HTMLScriptElement

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  entry = document.createElement('script')
  entry.type = 'module'
  entry.src = '/assets/index-old.js'
  document.head.append(entry)
  vi.stubGlobal('window', { location: { origin: 'https://opspilot.test', replace } })
  fetchIndex.mockResolvedValue({ ok: true, text: async () => html() })
  vi.stubGlobal('fetch', fetchIndex)
})

afterEach(() => {
  entry.remove()
  sessionStorage.clear()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('确认入口换版后替换导航到原站内目标，恢复标记只保存入口版本对', async () => {
  const error = moduleError()
  const pending = handleRouteError(error, '/application-configs/23?tab=history#version')
  expect(isHandledRouteError(error)).toBe(true)
  await pending
  expect(fetchIndex).toHaveBeenCalledWith('/index.html', { cache: 'no-store', signal: expect.any(AbortSignal) })
  expect(replace).toHaveBeenCalledWith('https://opspilot.test/application-configs/23?tab=history#version')
  expect(sessionStorage.length).toBe(1)
  expect(sessionStorage.getItem('opspilot_route_load_recovery')).toBe('/assets/index-old.js->/assets/index-new.js')
  expect(message.error).not.toHaveBeenCalled()
})

it('相同入口版本对只恢复一次，再次失败时可见提示', async () => {
  await handleRouteError(moduleError(), '/')
  await handleRouteError(moduleError(), '/profile')
  expect(replace).toHaveBeenCalledTimes(1)
  expect(message.error).toHaveBeenCalledTimes(1)
})

it('精确的 CSS 预加载失败也可在确认换版后恢复', async () => {
  await handleRouteError(new Error('Unable to preload CSS for /assets/Dashboard-old.css'), '/')
  expect(replace).toHaveBeenCalledOnce()
})

it.each(['Failed to fetch', '网络错误', 'chunk failed', '权限请求失败'])('普通错误 %s 只提示，不触发恢复', async text => {
  await handleRouteError(new Error(text), '/')
  expect(fetchIndex).not.toHaveBeenCalled()
  expect(replace).not.toHaveBeenCalled()
  expect(message.error).toHaveBeenCalledOnce()
})

it('入口版本相同不硬刷新', async () => {
  fetchIndex.mockResolvedValueOnce({ ok: true, text: async () => html('/assets/index-old.js') })
  await handleRouteError(moduleError(), '/')
  expect(replace).not.toHaveBeenCalled()
  expect(message.error).toHaveBeenCalledOnce()
})

it.each(['network', 'http', 'body'])('获取最新入口失败 %s 时收敛异常并提示', async kind => {
  if (kind === 'network') fetchIndex.mockRejectedValueOnce(new Error('离线'))
  else if (kind === 'http') fetchIndex.mockResolvedValueOnce({ ok: false, text: async () => html() })
  else fetchIndex.mockResolvedValueOnce({ ok: true, text: async () => { throw new Error('读取失败') } })
  await expect(handleRouteError(moduleError(), '/')).resolves.toBeUndefined()
  expect(replace).not.toHaveBeenCalled()
  expect(message.error).toHaveBeenCalledOnce()
})

it('版本检查 5 秒超时后中止请求并提示，不硬导航', async () => {
  vi.useFakeTimers()
  fetchIndex.mockImplementationOnce((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal!.addEventListener('abort', () => reject(new DOMException('检查超时', 'AbortError')))
  }))
  const pending = handleRouteError(moduleError(), '/')
  await vi.advanceTimersByTimeAsync(4999)
  expect(message.error).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1)
  await expect(pending).resolves.toBeUndefined()
  expect(fetchIndex.mock.calls[0]![1].signal.aborted).toBe(true)
  expect(replace).not.toHaveBeenCalled()
  expect(message.error).toHaveBeenCalledOnce()
})

it.each([
  html('https://evil.test/assets/index-new.js'), html('/assets/chunk-new.js'), '<html></html>',
  `${html()}<script type="module" src="/assets/index-other.js"></script>`,
])('最新页面缺少唯一可信入口时不恢复', async content => {
  fetchIndex.mockResolvedValueOnce({ ok: true, text: async () => content })
  await handleRouteError(moduleError(), '/')
  expect(replace).not.toHaveBeenCalled()
  expect(message.error).toHaveBeenCalledOnce()
})

it('当前页面入口不可信时不恢复', async () => {
  entry.src = 'https://evil.test/assets/index-old.js'
  await handleRouteError(moduleError(), '/')
  expect(replace).not.toHaveBeenCalled()
  expect(message.error).toHaveBeenCalledOnce()
})

it.each(['https://evil.test/', '//evil.test/', '/\\evil.test/', 'javascript:alert(1)', '/api/v1/auth/logout', '/assets/index-new.js', '/index.html'])('非法目标 %s 不允许硬导航', async target => {
  await handleRouteError(moduleError(), target)
  expect(replace).not.toHaveBeenCalled()
  expect(message.error).toHaveBeenCalledOnce()
  expect(sessionStorage.length).toBe(0)
})
