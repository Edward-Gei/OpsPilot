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
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: api.routeQuery, path: '/job/templates/processes' }),
  useRouter: () => ({ replace: vi.fn() }),
}))

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

it('关联流程模板打开详情并在搜索框中只列出该流程', async () => {
  api.routeQuery = { id: '13', related: 'ticket' }
  api.listProcessTemplates.mockImplementation(async (params: { process_id?: number }) =>
    params.process_id === 13
      ? { items: [{ id: 13, name: '部署流程', status: 'enabled' }], total: 1 }
      : { items: [{ id: 13, name: '部署流程', status: 'enabled' },
          { id: 14, name: '部署流程扩展', status: 'enabled' }], total: 2 })
  const wrapper = mount(ProcessTemplateList, { attachTo: document.body,
    global: { stubs: { ProcessTemplateEditor: true } } })
  await flushPromises()
  expect(wrapper.find<HTMLInputElement>('input[placeholder="搜索流程模板"]').element.value).toBe('部署流程')
  expect(api.listProcessTemplates).toHaveBeenCalledWith(expect.objectContaining({ process_id: 13 }))
  expect(wrapper.findAll('.ant-table-tbody tr[data-row-key]')).toHaveLength(1)
  wrapper.unmount()
})

it('修改关联流程模板搜索词后恢复普通列表搜索', async () => {
  api.routeQuery = { id: '13', related: 'ticket' }
  const wrapper = mount(ProcessTemplateList, { attachTo: document.body,
    global: { stubs: { ProcessTemplateEditor: true } } })
  await flushPromises()
  await wrapper.find('input[placeholder="搜索流程模板"]').setValue('其他流程')
  await flushPromises()
  const searchCall = api.listProcessTemplates.mock.calls.find(([params]) => params.keyword === '其他流程')
  expect(searchCall?.[0].process_id).toBeUndefined()
  wrapper.unmount()
})
