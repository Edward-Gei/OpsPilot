// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { config, flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import { createPinia } from 'pinia'
import HostList from './HostList.vue'

const api = vi.hoisted(() => ({
  routeQuery: {} as Record<string, string>,
  databaseRead: false,
  listHosts: vi.fn(), getHost: vi.fn(),
}))
vi.mock('@/api/cmdb', () => api)
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: (perm: string) => perm === 'database:read' && api.databaseRead }) }))
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
  api.databaseRead = false
  api.listHosts.mockImplementation(async (params: { host_id?: number; keyword?: string }) =>
    params.host_id === 21 ? { items: [first], total: 1 }
      : params.keyword === 'other' ? { items: [], total: 0 }
        : { items: [first, second], total: 2 })
  api.getHost.mockResolvedValue({ ...first, apps: [] })
})

it.each([true, false])('承载数据库以三列表格展示，读取权限为%s时保留正确链接边界', async allowed => {
  api.databaseRead = allowed
  api.routeQuery = { id: '21' }
  api.getHost.mockResolvedValue({ ...first, apps: [], database_instances: [
    { id: 31, name: '交易实例', engine: 'mysql', enabled: true },
    { id: 32, name: '分析实例', engine: 'postgresql', enabled: false },
    { id: 33, name: '文档实例', engine: 'mongodb', enabled: true },
    { id: 34, name: '数据仓库', engine: 'clickhouse', enabled: true },
    { id: 35, name: '缓存实例', engine: 'redis', enabled: true },
  ] })
  const wrapper = mount(HostList, { attachTo: document.body })
  await flushPromises()
  const title = [...document.querySelectorAll('.detail-apps-title')].find(item => item.textContent?.startsWith('承载数据库实例'))!
  const table = title.nextElementSibling?.querySelector('table')
  expect(table).toBeTruthy()
  expect([...table!.querySelectorAll('thead th')].map(item => item.textContent)).toEqual(['实例名称', '引擎', '状态'])
  expect([...table!.querySelectorAll('tbody tr')].map(row => row.children[1]?.textContent)).toEqual(['MySQL', 'PostgreSQL', 'MongoDB', 'ClickHouse', 'Redis'])
  expect([...table!.querySelectorAll('tbody tr')].map(row => row.children[2]?.textContent)).toEqual(['启用', '停用', '启用', '启用', '启用'])
  expect(table!.querySelector('a[href="/database/instances?instance_id=31&related=host"]')?.textContent || null).toBe(allowed ? '交易实例' : null)
  expect(table!.textContent).toContain('交易实例')
  wrapper.unmount()
})
afterEach(() => { document.body.innerHTML = '' })

it('清除关联主机搜索词只刷新一次完整列表', async () => {
  api.routeQuery = { id: '21', related: 'app' }
  const wrapper = mount(HostList, { attachTo: document.body })
  await flushPromises()
  expect(wrapper.get<HTMLInputElement>('.toolbar .kw input').element.value).toBe('shared-host')
  const beforeClear = api.listHosts.mock.calls.length
  await wrapper.get('.toolbar .kw .ant-input-clear-icon').trigger('click')
  await flushPromises()
  expect(api.listHosts).toHaveBeenCalledTimes(beforeClear + 1)
  expect(api.listHosts).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, keyword: undefined, host_id: undefined }))
  wrapper.unmount()
})

it.each(['app', 'database'])('从%s关联主机跳转回填搜索框并精确展示一条，改词后恢复普通搜索', async (related) => {
  api.routeQuery = { id: '21', related }
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
