// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { message } from 'ant-design-vue'
import type { RouteComponent, Router } from 'vue-router'

vi.mock('vue-router', async importOriginal => {
  const actual = await importOriginal<typeof import('vue-router')>()
  return { ...actual, createWebHistory: () => actual.createMemoryHistory() }
})
vi.mock('@/layouts/BasicLayout.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({
  isLoggedIn: () => true, userInfo: {}, hasPerm: () => true, hasAnyPerm: () => true,
}) }))
vi.mock('@/api/token', () => ({ expireIfNeeded: () => false, restoreTokensFromPeer: async () => false }))
vi.mock('ant-design-vue', () => ({ message: { error: vi.fn() } }))

const replace = vi.fn()
const fetchIndex = vi.fn()
let entry: HTMLScriptElement
let router: Router

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

const html = (version: string) => `<script type="module" src="/assets/index-${version}.js"></script>`

beforeEach(async () => {
  vi.clearAllMocks()
  vi.resetModules()
  sessionStorage.clear()
  entry = document.createElement('script')
  entry.type = 'module'
  entry.src = '/assets/index-old.js'
  document.head.append(entry)
  vi.stubGlobal('window', { location: { origin: 'https://opspilot.test', replace }, addEventListener: vi.fn() })
  vi.stubGlobal('fetch', fetchIndex)
  router = (await import('./index')).default
  router.addRoute({ path: '/start', component: { template: '<div>起点</div>' } })
  router.addRoute({ path: '/failed-a', component: async () => {
    throw new TypeError('Failed to fetch dynamically imported module: /assets/A-old.js')
  } })
  await router.push('/start')
})

afterEach(() => {
  router.options.history.destroy()
  entry.remove()
  sessionStorage.clear()
  vi.unstubAllGlobals()
})

it.each([false, true])('B 导航已开始且完成状态为 %s 时，A 的迟到换版检查不拉回旧目标', async completed => {
  const version = deferred<{ ok: boolean; text: () => Promise<string> }>()
  fetchIndex.mockReturnValueOnce(version.promise)
  await expect(router.push('/failed-a')).rejects.toThrow('Failed to fetch dynamically imported module')
  expect(fetchIndex).toHaveBeenCalledOnce()

  const page = deferred<RouteComponent>()
  const loadB = vi.fn(() => page.promise)
  router.addRoute({ path: '/next-b', component: loadB })
  const navigation = router.push('/next-b')
  await flushPromises()
  expect(loadB).toHaveBeenCalledOnce()
  if (completed) {
    page.resolve({ template: '<div>B</div>' })
    await navigation
    expect(router.currentRoute.value.path).toBe('/next-b')
  } else expect(router.currentRoute.value.path).toBe('/start')

  version.resolve({ ok: true, text: async () => html('new') })
  await flushPromises()
  const staleReplacement = replace.mock.calls.length
  const staleErrors = vi.mocked(message.error).mock.calls.length
  if (!completed) {
    page.resolve({ template: '<div>B</div>' })
    await navigation
  }
  expect(staleReplacement).toBe(0)
  expect(staleErrors).toBe(0)
  expect(router.currentRoute.value.path).toBe('/next-b')
})

it('当前失败导航的同版检查仍提示错误', async () => {
  const version = deferred<{ ok: boolean; text: () => Promise<string> }>()
  fetchIndex.mockReturnValueOnce(version.promise)
  await expect(router.push('/failed-a')).rejects.toThrow()
  version.resolve({ ok: true, text: async () => html('old') })
  await flushPromises()
  expect(message.error).toHaveBeenCalledOnce()
  expect(replace).not.toHaveBeenCalled()
})

it('B 已开始时，A 迟到的同版检查不再提示旧错误', async () => {
  const body = deferred<string>()
  fetchIndex.mockResolvedValueOnce({ ok: true, text: () => body.promise })
  await expect(router.push('/failed-a')).rejects.toThrow()
  const page = deferred<RouteComponent>()
  router.addRoute({ path: '/next-b', component: () => page.promise })
  const navigation = router.push('/next-b')
  await flushPromises()
  body.resolve(html('old'))
  await flushPromises()
  const staleErrors = vi.mocked(message.error).mock.calls.length
  page.resolve({ template: '<div>B</div>' })
  await navigation
  expect(staleErrors).toBe(0)
  expect(replace).not.toHaveBeenCalled()
})
