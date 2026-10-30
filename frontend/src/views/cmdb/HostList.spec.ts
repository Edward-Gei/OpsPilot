// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { config, flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import { createPinia } from 'pinia'
import HostList from './HostList.vue'

const api = vi.hoisted(() => ({
  routeQuery: {} as Record<string, string>,
  listHosts: vi.fn(), getHost: vi.fn(),
}))
vi.mock('@/api/cmdb', () => api)
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: () => false }) }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: api.routeQuery, path: '/cmdb/hosts' }),
  useRouter: () => ({ replace: vi.fn() }),
}))

const first = { id: 21, hostname: 'shared-host', ip: '192.0.2.21', project: 'mitrade',
  environment: 'prod', status: 'online', ssh_port: 22, cpu_cores: null, memory_gb: null,
  disk_gb: null, created_at: null }
const second = { ...first, id: 22, ip: '192.0.2.22' }

config.global.plugins = [Antd, createPinia()]
class TestResizeObserver { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal('ResizeObserver', TestResizeObserver)
const getComputedStyle = window.getComputedStyle.bind(window)
window.getComputedStyle = ((element: Element) => getComputedStyle(element)) as typeof window.getComputedStyle
window.matchMedia = vi.fn().mockImplementation((media: string) => ({
  matches: false, media, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
  addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  api.routeQuery = {}
  api.listHosts.mockImplementation(async (params: { host_id?: number; keyword?: string }) =>
    params.host_id === 21 ? { items: [first], total: 1 }
      : params.keyword === 'other' ? { items: [], total: 0 }
        : { items: [first, second], total: 2 })
  api.getHost.mockResolvedValue({ ...first, apps: [] })
})
afterEach(() => { document.body.innerHTML = '' })

it('关联主机跳转回填搜索框并精确展示一条，改词后恢复普通搜索', async () => {
  api.routeQuery = { id: '21', related: 'app' }
  const wrapper = mount(HostList, { attachTo: document.body })
  await flushPromises()

  expect(document.querySelector('.ant-drawer-title')?.textContent).toBe('shared-host')
  const input = document.querySelector<HTMLInputElement>('.toolbar .kw input')!
  expect(input.value).toBe('shared-host')
  expect(api.listHosts).toHaveBeenCalledWith(expect.objectContaining({ host_id: 21 }))
  expect(document.body.textContent).toContain('共 1 台主机')

  input.value = 'other'
  input.dispatchEvent(new Event('input', { bubbles: true }))
  input.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
  expect(api.listHosts).toHaveBeenCalledWith(expect.objectContaining({ keyword: 'other', host_id: undefined }))
  wrapper.unmount()
})
