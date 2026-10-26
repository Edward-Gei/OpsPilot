// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { config, flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import TicketTemplateList from './TicketTemplateList.vue'

const api = vi.hoisted(() => ({ listTemplates: vi.fn(), getTemplate: vi.fn(), listProcessTemplates: vi.fn(),
  routeQuery: {} as Record<string, string>, cmdbRead: true }))
vi.mock('@/api/job', () => api)
vi.mock('@/api/system', () => ({ listRoleOptions: async () => ({ items: [] }) }))
vi.mock('@/api/jobHost', () => ({ listJobHosts: async () => ({ items: [] }) }))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: (perm: string) => perm === 'cmdb:read' && api.cmdbRead }) }))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: api.routeQuery }) }))

config.global.plugins = [Antd]
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
  api.routeQuery = { id: '13' }
  api.cmdbRead = true
  api.listTemplates.mockResolvedValue({ items: [], total: 0 })
  api.listProcessTemplates.mockResolvedValue({ items: [], total: 0 })
  api.getTemplate.mockResolvedValue({ id: 13, name: '订单发布入口', type: 'release', status: 'enabled',
    app_ids: [7], apps: [{ id: 7, name: '订单应用' }], params_schema: [], notify_rules: [] })
})
afterEach(() => { document.body.innerHTML = '' })

it('列表使用响应中的关联名称，不依赖前 100 项候选', async () => {
  api.routeQuery = {}
  api.listTemplates.mockResolvedValue({ items: [{ id: 21, name: '部署入口', type: 'release',
    job_host_id: 121, job_host_name: '远端作业主机', process_template_id: 122,
    process_template_name: '远端流程模板', visible_role_ids: [], status: 'enabled',
    params_schema: [], notify_rules: [] }], total: 1 })
  const wrapper = mount(TicketTemplateList, { global: { stubs: { TicketTemplateEditor: true } } })
  await flushPromises()
  expect(wrapper.text()).toContain('远端作业主机')
  expect(wrapper.find('a[href="/job/templates/processes?id=122"]').text()).toBe('远端流程模板')
  wrapper.unmount()
})

it('工单模板链接按 ID 直达只读详情，并展示适用应用链接', async () => {
  const wrapper = mount(TicketTemplateList, { attachTo: document.body,
    global: { stubs: { TicketTemplateEditor: true } } })
  await flushPromises()
  expect(document.querySelector('.ant-drawer-title')?.textContent).toBe('订单发布入口')
  expect(document.querySelector('a[href="/cmdb/apps?id=7"]')?.textContent).toBe('订单应用')
  expect(wrapper.findAll('button').some(button => button.text().includes('编辑'))).toBe(false)
  wrapper.unmount()
})

it('没有 CMDB 读取权限时展示应用名称并禁用详情链接', async () => {
  api.cmdbRead = false
  const wrapper = mount(TicketTemplateList, { attachTo: document.body,
    global: { stubs: { TicketTemplateEditor: true } } })
  await flushPromises()
  expect(document.body.textContent).toContain('订单应用')
  expect(document.querySelector('a[href="/cmdb/apps?id=7"]')).toBeNull()
  wrapper.unmount()
})

it.each(['0', '-1', '1.5', 'invalid'])('无效工单模板详情 ID %s 不打开详情', async id => {
  api.routeQuery = { id }
  const wrapper = mount(TicketTemplateList, { global: { stubs: { TicketTemplateEditor: true } } })
  await flushPromises()
  expect(document.querySelector('.ant-drawer-title')).toBeNull()
  wrapper.unmount()
})
