// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { config, flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import { createPinia } from 'pinia'
import AppList from './AppList.vue'
import HostList from './HostList.vue'

const api = vi.hoisted(() => ({ listApps: vi.fn(), getApp: vi.fn(), listHosts: vi.fn(), getHost: vi.fn(), listAppSystems: vi.fn(), exportApps: vi.fn(),
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
  api.listAppSystems.mockResolvedValue({ items: ['订单系统', '订单系统扩展'] })
  api.getApp.mockResolvedValue({ ...app, hosts: [], config_files: [{
    id: 11, name: 'common.yaml', platform_instance_name: '生产 Nacos', provider: 'nacos',
    locator: { namespace: 'prod', group: 'DEFAULT_GROUP', data_id: 'common.yaml' },
    status: 'active', drift_status: 'clean',
  }] })
})
afterEach(() => { document.body.innerHTML = '' })

it('应用详情以三列表格展示关联模板、流程模板和启停状态，并保留详情链接', async () => {
  api.routeQuery = { id: '7' }
  api.getApp.mockResolvedValue({ ...app, hosts: [], config_files: [],
    ticket_templates: [
      { id: 13, name: '订单发布入口', status: 'enabled', process_template_id: 23, process_template_name: '订单发布流程' },
      { id: 14, name: '订单回滚入口', status: 'disabled', process_template_id: 24, process_template_name: '订单回滚流程' },
    ] })
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  expect(document.body.textContent).toContain('关联工单模板（2）')
  const table = document.querySelector('a[href="/job/templates/tickets?id=13&related=app"]')?.closest('table')
  expect(table).not.toBeNull()
  expect([...table!.querySelectorAll('thead th')].map(item => item.textContent)).toEqual(['模板名称', '流程模板', '状态'])
  expect(document.querySelector('a[href="/job/templates/processes?id=23&related=app"]')?.textContent).toBe('订单发布流程')
  expect([...table!.querySelectorAll('tbody tr')].map(item => item.lastElementChild?.textContent)).toEqual(['启用', '停用'])
  expect(document.querySelector('a[href="/job/templates/tickets?id=13&related=app"]')?.textContent).toBe('订单发布入口')
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
  expect(document.querySelector('a[href="/cmdb/hosts?id=21&related=app"]')?.textContent).toBe('demo-host')
  expect(document.querySelector('a[href="/application-configs?file_id=11&related=app"]')?.textContent).toBe('common.yaml')
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
  expect(document.querySelector('a[href="/application-configs?file_id=11&related=app"]')).toBeNull()
  wrapper.unmount()
})

it('应用详情链接按 ID 直达，不依赖当前分页是否包含该应用', async () => {
  api.routeQuery = { id: '7' }
  api.listApps.mockResolvedValue({ items: [], total: 0 })
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  expect(document.querySelector('.ant-drawer-title')?.textContent).toBe('订单应用')
  expect(api.listApps.mock.calls.every(([params]) => params.app_id === undefined)).toBe(true)
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
  expect(document.querySelector('a[href="/cmdb/apps?id=7&related=host"]')?.textContent).toBe('订单应用')
  wrapper.unmount()
})

it('从主机关联应用跳转时打开详情并只列出该应用', async () => {
  api.routeQuery = { id: '7', related: 'host' }
  api.listApps.mockImplementation(async (params: { app_id?: number }) =>
    params.app_id === 7 ? { items: [app], total: 1 } : { items: [app, { ...app, id: 8, name: '订单应用扩展' }], total: 2 })
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  expect(document.querySelector('.ant-drawer-title')?.textContent).toBe('订单应用')
  expect(wrapper.find<HTMLInputElement>('input[placeholder="搜索应用信息"]').element.value).toBe('订单应用')
  expect(api.listApps).toHaveBeenCalledWith(expect.objectContaining({ app_id: 7 }))
  expect(wrapper.findAll('.ant-table-tbody tr[data-row-key]')).toHaveLength(1)
  wrapper.unmount()
})

it.each(['ticket', 'config'])('从 %s 关联应用跳转时搜索框回填并只列出目标应用', async (related) => {
  api.routeQuery = { id: '7', related }
  api.listApps.mockImplementation(async (params: { app_id?: number }) =>
    params.app_id === 7 ? { items: [app], total: 1 } : { items: [app, { ...app, id: 8, name: '订单应用扩展' }], total: 2 })
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  expect(document.querySelector('.ant-drawer-title')?.textContent).toBe('订单应用')
  expect(wrapper.find<HTMLInputElement>('input[placeholder="搜索应用信息"]').element.value).toBe('订单应用')
  expect(api.listApps).toHaveBeenCalledWith(expect.objectContaining({ app_id: 7 }))
  expect(wrapper.findAll('.ant-table-tbody tr[data-row-key]')).toHaveLength(1)
  wrapper.unmount()
})

it('关联应用搜索词未修改时按回车仍保留精确结果', async () => {
  api.routeQuery = { id: '7', related: 'host' }
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  await wrapper.find('input[placeholder="搜索应用信息"]').trigger('keydown.enter')
  await flushPromises()
  expect(api.listApps.mock.lastCall?.[0].app_id).toBe(7)
  wrapper.unmount()
})

it('修改关联应用搜索词后恢复普通搜索', async () => {
  api.routeQuery = { id: '7', related: 'host' }
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  await wrapper.find('input[placeholder="搜索应用信息"]').setValue('其他应用')
  await flushPromises()
  const params = api.listApps.mock.lastCall?.[0]
  expect(params.keyword).toBe('其他应用')
  expect(params.app_id).toBeUndefined()
  wrapper.unmount()
})

it('清空关联应用搜索词后恢复完整列表', async () => {
  api.routeQuery = { id: '7', related: 'host' }
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  await wrapper.find('input[placeholder="搜索应用信息"]').setValue('')
  await flushPromises()
  const params = api.listApps.mock.lastCall?.[0]
  expect(params.keyword).toBeUndefined()
  expect(params.app_id).toBeUndefined()
  wrapper.unmount()
})

it('旧的关联应用列表响应不会覆盖修改后的搜索结果', async () => {
  api.routeQuery = { id: '7', related: 'host' }
  const other = { ...app, id: 8, name: '其他应用' }
  let finishFocused!: (value: { items: typeof app[]; total: number }) => void
  api.listApps.mockImplementation(async (params: { app_id?: number; keyword?: string }) => {
    if (params.app_id === 7) return new Promise((resolve) => { finishFocused = resolve })
    if (params.keyword === '其他应用') return { items: [other], total: 1 }
    return { items: [app], total: 1 }
  })
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  await wrapper.find('input[placeholder="搜索应用信息"]').setValue('其他应用')
  await flushPromises()
  finishFocused({ items: [app], total: 1 })
  await flushPromises()
  expect(wrapper.find('.ant-table-tbody tr[data-row-key]').text()).toContain('其他应用')
  wrapper.unmount()
})

it('关联应用详情不存在时恢复普通列表', async () => {
  api.routeQuery = { id: '7', related: 'host' }
  api.getApp.mockRejectedValue(new Error('应用不存在'))
  api.listApps.mockImplementation(async (params: { app_id?: number }) =>
    params.app_id === 7 ? { items: [], total: 0 } : { items: [app], total: 1 })
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  expect(wrapper.find('.ant-table-tbody tr[data-row-key]').exists()).toBe(true)
  expect(wrapper.find('.ant-table-tbody tr[data-row-key]').text()).toContain('订单应用')
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
    .filter((header) => header.textContent?.trim() && header.textContent.trim() !== '操作')
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

it('主机每个非操作列表头均有排序入口，资源配置排序传给后端', async () => {
  api.listHosts.mockResolvedValue({ items: [], total: 0 })
  const wrapper = mount(HostList, { attachTo: document.body })
  await flushPromises()
  const headers = [...document.querySelectorAll<HTMLElement>('.ant-table-thead th')]
    .filter((header) => header.textContent?.trim() && header.textContent.trim() !== '操作')
  expect(headers).toHaveLength(14)
  expect(headers.every((header) => !!header.querySelector('.ant-table-column-sorter'))).toBe(true)
  headers.find((header) => header.textContent?.includes('资源配置'))!
    .querySelector<HTMLElement>('.ant-table-column-sorters')!.click()
  await flushPromises()
  expect(api.listHosts).toHaveBeenLastCalledWith(expect.objectContaining({
    sort_by: 'spec', sort_order: 'asc', page: 1,
  }))
  wrapper.unmount()
})

it('所属系统下拉展示已有名称，并精确筛选应用', async () => {
  const wrapper = mount(AppList, { attachTo: document.body })
  await flushPromises()
  expect(api.listAppSystems).toHaveBeenCalled()
  const select = [...document.querySelectorAll<HTMLElement>('.ant-select')]
    .find((item) => item.querySelector('.ant-select-selection-placeholder')?.textContent === '所属系统')!
  select.querySelector('.ant-select-selector')!
    .dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
  await flushPromises()
  const option = [...document.querySelectorAll<HTMLElement>('.ant-select-item-option')]
    .find((item) => item.textContent?.includes('订单系统'))!
  option.click()
  await flushPromises()
  expect(api.listApps).toHaveBeenLastCalledWith(expect.objectContaining({
    system_name: '订单系统', page: 1,
  }))
  const exportButton = [...document.querySelectorAll<HTMLButtonElement>('button')]
    .find((button) => button.textContent?.includes('导出'))!
  exportButton.click()
  await flushPromises()
  expect(api.exportApps).toHaveBeenCalledWith(expect.objectContaining({ system_name: '订单系统' }))
  wrapper.unmount()
})
