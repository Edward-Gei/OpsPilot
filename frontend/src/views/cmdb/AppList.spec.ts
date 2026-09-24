// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { config, flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import { createPinia } from 'pinia'
import AppList from './AppList.vue'

const api = vi.hoisted(() => ({ listApps: vi.fn(), getApp: vi.fn() }))
vi.mock('@/api/cmdb', () => api)
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: () => true }) }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {}, path: '/cmdb/apps' }),
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
  api.listApps.mockResolvedValue({ items: [app], total: 1 })
  api.getApp.mockResolvedValue({ ...app, hosts: [], config_files: [{
    id: 11, name: 'common.yaml', platform_instance_name: '生产 Nacos', provider: 'nacos',
    locator: { namespace: 'prod', group: 'DEFAULT_GROUP', data_id: 'common.yaml' },
    status: 'active', drift_status: 'clean',
  }] })
})
afterEach(() => { document.body.innerHTML = '' })

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
