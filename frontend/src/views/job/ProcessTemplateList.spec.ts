// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { config, flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import ProcessTemplateList from './ProcessTemplateList.vue'

const api = vi.hoisted(() => ({ listProcessTemplates: vi.fn(), getProcessTemplate: vi.fn(),
  routeQuery: {} as Record<string, string> }))
vi.mock('@/api/job', () => api)
vi.mock('@/api/system', () => ({ listRoleOptions: async () => ({ items: [] }) }))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: () => false }) }))
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
  api.routeQuery = {}
  api.listProcessTemplates.mockResolvedValue({ items: [], total: 0 })
  api.getProcessTemplate.mockResolvedValue({ id: 13, name: '部署流程', status: 'enabled', steps: [] })
})
afterEach(() => { document.body.innerHTML = '' })

it('流程链接按 ID 打开只读详情，不依赖分页记录或编辑权限', async () => {
  api.routeQuery = { id: '13' }
  const wrapper = mount(ProcessTemplateList, { attachTo: document.body,
    global: { stubs: { ProcessTemplateEditor: true } } })
  await flushPromises()
  expect(api.getProcessTemplate).toHaveBeenCalledWith(13)
  expect(document.querySelector('.ant-drawer-title')?.textContent).toBe('部署流程')
  expect(document.body.textContent).toContain('模板 ID')
  expect(wrapper.findAll('button').some(button => button.text().includes('编辑'))).toBe(false)
  wrapper.unmount()
})

it.each(['0', '-1', '1.5', 'invalid'])('无效详情 ID %s 不发起查询', async id => {
  api.routeQuery = { id }
  const wrapper = mount(ProcessTemplateList, { global: { stubs: { ProcessTemplateEditor: true } } })
  await flushPromises()
  expect(api.getProcessTemplate).not.toHaveBeenCalled()
  wrapper.unmount()
})
