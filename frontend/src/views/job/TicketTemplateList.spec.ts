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
