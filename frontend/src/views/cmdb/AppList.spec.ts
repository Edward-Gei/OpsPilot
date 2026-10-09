// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { config, flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import { createPinia } from 'pinia'
import AppList from './AppList.vue'
import HostList from './HostList.vue'

const api = vi.hoisted(() => ({ listApps: vi.fn(), getApp: vi.fn(), listHosts: vi.fn(), getHost: vi.fn(),
  routeQuery: {} as Record<string, string>, configRead: true }))
vi.mock('@/api/cmdb', () => api)
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: (perm: string) => perm !== 'config:read' || api.configRead }) }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: api.routeQuery, path: '/cmdb/apps' }),
  useRouter: () => ({ replace: vi.fn() }),
}))

const app = {
  id: 7, name: '订单应用', description: null, language: 'Go', deploy_type: 'shell',
  project_type: 'backend', business_line: 'mitrade', system_name: null, service_level: '核心服务',
  ops_owner: null, dev_owner: null, repo_url: null, service_port: '8080', cpu_quota: null,
  mem_quota: null, host_count: 0, host_ips: [], created_at: null,
}

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
  api.configRead = true
  api.listApps.mockResolvedValue({ items: [app], total: 1 })
  api.getApp.mockResolvedValue({ ...app, hosts: [], config_files: [{
    id: 11, name: 'common.yaml', platform_instance_name: '生产 Nacos', provider: 'nacos',
    locator: { namespace: 'prod', group: 'DEFAULT_GROUP', data_id: 'common.yaml' },
    status: 'active', drift_status: 'clean',
  }] })
})
afterEach(() => { document.body.innerHTML = '' })

it('应用详情展示关联工单模板名称并链接到模板详情', async () => {
  api.routeQuery = { id: '7' }
  api.getApp.mockResolvedValue({ ...app, hosts: [], config_files: [],
    ticket_templates: [{ id: 13, name: '订单发布入口', status: 'enabled' }] })
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  expect(document.body.textContent).toContain('关联工单模板（1）')
  expect(document.querySelector('a[href="/job/templates/tickets?id=13"]')?.textContent).toBe('订单发布入口')
  wrapper.unmount()
})

it('点击应用名打开该应用详情，关联主机和配置提供对应详情链接', async () => {
  api.getApp.mockResolvedValue({ ...app, hosts: [{ id: 21, hostname: 'demo-host', ip: '192.0.2.1', environment: 'prod' }],
    config_files: [{ id: 11, name: 'common.yaml', platform_instance_name: 'Nacos', provider: 'nacos',
      locator: { group: 'DEFAULT_GROUP', data_id: 'common.yaml' }, status: 'active', drift_status: 'clean' }] })
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  const nameLink = wrapper.find('a[href="/cmdb/apps?id=7"]')
  expect(nameLink.exists()).toBe(true)
  await nameLink.trigger('click')
  await flushPromises()
  expect(document.querySelector('.ant-drawer-title')?.textContent).toBe('订单应用')
  expect(document.querySelector('a[href="/cmdb/hosts?id=21"]')?.textContent).toBe('demo-host')
  expect(document.querySelector('a[href="/application-configs/11"]')?.textContent).toBe('common.yaml')
  wrapper.unmount()
})

it('没有配置读取权限时仍展示关联配置名称，但不提供详情链接', async () => {
  api.configRead = false
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  const detailButton = [...document.querySelectorAll<HTMLButtonElement>('button')]
    .find(button => button.textContent?.includes('详情'))!
  detailButton.click()
  await flushPromises()
  expect(document.body.textContent).toContain('common.yaml')
  expect(document.querySelector('a[href="/application-configs/11"]')).toBeNull()
  wrapper.unmount()
})

it('应用详情链接按 ID 直达，不依赖当前分页是否包含该应用', async () => {
  api.routeQuery = { id: '7' }
  api.listApps.mockResolvedValue({ items: [], total: 0 })
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  expect(document.querySelector('.ant-drawer-title')?.textContent).toBe('订单应用')
  wrapper.unmount()
})

it('主机详情链接按 ID 直达，不依赖当前列表记录', async () => {
  api.routeQuery = { id: '21' }
  api.listHosts.mockResolvedValue({ items: [], total: 0 })
  api.getHost.mockResolvedValue({ id: 21, hostname: 'demo-host', ip: '192.0.2.1', project: 'mitrade',
    environment: 'prod', status: 'online', ssh_port: 22, apps: [app], created_at: null })
  const wrapper = mount(HostList, { attachTo: document.body })
  await flushPromises()
  expect(document.querySelector('.ant-drawer-title')?.textContent).toBe('demo-host')
  expect(document.body.textContent).toContain('192.0.2.1')
  expect(document.querySelector('a[href="/cmdb/apps?id=7"]')?.textContent).toBe('订单应用')
  wrapper.unmount()
})

it('应用详情展示关联配置文件元信息', async () => {
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  const detailButton = [...document.querySelectorAll<HTMLButtonElement>('button')]
    .find((button) => button.textContent?.includes('详情'))!
  detailButton.click()
  await flushPromises()
  expect(document.body.textContent).toContain('关联配置文件（1）')
  expect(document.body.textContent).toContain('common.yaml')
  expect(document.body.textContent).toContain('生产 Nacos')
  expect(document.body.textContent).toContain('DEFAULT_GROUP')
  wrapper.unmount()
})

it('每个非操作列表头均有排序入口，服务端口排序传给后端', async () => {
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  const headers = [...document.querySelectorAll<HTMLElement>('.ant-table-thead th')]
    .filter((header) => header.textContent?.trim() && !header.textContent.includes('操作'))
  expect(headers).toHaveLength(15)
  expect(headers.every((header) => !!header.querySelector('.ant-table-column-sorter'))).toBe(true)
  headers.find((header) => header.textContent?.includes('服务端口'))!
    .querySelector<HTMLElement>('.ant-table-column-sorters')!.click()
  await flushPromises()
  expect(api.listApps).toHaveBeenLastCalledWith(expect.objectContaining({
    sort_by: 'service_port', sort_order: 'asc', page: 1,
  }))
  wrapper.unmount()
})
