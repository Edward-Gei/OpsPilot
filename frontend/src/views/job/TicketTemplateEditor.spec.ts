// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { config, flushPromises, mount } from '@vue/test-utils'
import Antd, { message, Select } from 'ant-design-vue'
import TicketTemplateEditor from './TicketTemplateEditor.vue'

const api = vi.hoisted(() => ({ getTemplate: vi.fn(), createTemplate: vi.fn(), updateTemplate: vi.fn(),
  listProcessTemplates: vi.fn(), listApps: vi.fn(), cmdbRead: true }))
vi.mock('@/api/job', () => api)
vi.mock('@/api/cmdb', () => ({ listApps: api.listApps }))
vi.mock('@/api/system', () => ({ listRoleOptions: async () => ({ items: [] }) }))
vi.mock('@/api/jobHost', () => ({ listJobHosts: async () => ({ items: [{ id: 1, name: '作业主机', enabled: true }] }) }))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: (perm: string) => perm === 'cmdb:read' && api.cmdbRead }) }))

config.global.plugins = [Antd]
class TestResizeObserver { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal('ResizeObserver', TestResizeObserver)
const getComputedStyle = window.getComputedStyle.bind(window)
window.getComputedStyle = ((element: Element) => getComputedStyle(element)) as typeof window.getComputedStyle
window.matchMedia = vi.fn().mockImplementation((media: string) => ({ matches: false, media,
  addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }))

beforeEach(() => {
  vi.clearAllMocks()
  api.cmdbRead = true
  api.listProcessTemplates.mockResolvedValue({ items: [{ id: 2, name: '流程', status: 'enabled' }] })
  api.listApps.mockImplementation(async ({ page }: { page: number }) => ({ total: 101,
    items: page === 1 ? Array.from({ length: 100 }, (_, i) => ({ id: i + 1, name: `应用${i + 1}` }))
      : [{ id: 101, name: '末页应用' }] }))
  api.getTemplate.mockResolvedValue({ id: 13, name: '发布模板', type: 'release', job_host_id: 1,
    process_template_id: 2, app_ids: [101], apps: [{ id: 101, name: '末页应用' }], status: 'enabled',
    params_schema: [], credential_refs: [], notify_rules: [], visible_role_ids: [] })
  api.createTemplate.mockResolvedValue({ id: 14 })
  api.updateTemplate.mockResolvedValue({ id: 13 })
})
afterEach(() => { message.destroy(); document.body.innerHTML = '' })

it('复制继承应用绑定，候选包含末页应用，保存后源模板不变', async () => {
  const wrapper = mount(TicketTemplateEditor, { props: { open: true, templateId: null, copyFromId: 13 },
    attachTo: document.body, global: { stubs: { CodeEditor: true } } })
  await flushPromises()
  const select = wrapper.findAllComponents(Select).find(item => item.props('mode') === 'multiple')!
  expect(select.props('options')).toHaveLength(101)
  expect(select.props('value')).toEqual([101])
  const save = document.querySelector<HTMLButtonElement>('.ant-modal-footer .ant-btn-primary')!
  save.click()
  await flushPromises()
  expect(api.createTemplate).toHaveBeenCalledWith(expect.objectContaining({ name: '发布模板-副本', app_ids: [101] }))
  expect(api.updateTemplate).not.toHaveBeenCalled()
  wrapper.unmount()
})

it('编辑模板可清空全部应用绑定', async () => {
  const wrapper = mount(TicketTemplateEditor, { props: { open: true, templateId: 13 },
    attachTo: document.body, global: { stubs: { CodeEditor: true } } })
  await flushPromises()
  wrapper.findAllComponents(Select).find(item => item.props('mode') === 'multiple')!.vm.$emit('update:value', [])
  await flushPromises()
  document.querySelector<HTMLButtonElement>('.ant-modal-footer .ant-btn-primary')!.click()
  await flushPromises()
  expect(api.updateTemplate).toHaveBeenCalledWith(13, expect.objectContaining({ app_ids: [] }))
  wrapper.unmount()
})

it('没有应用读取权限时禁用绑定编辑，保存不提交应用字段', async () => {
  api.cmdbRead = false
  const wrapper = mount(TicketTemplateEditor, { props: { open: true, templateId: 13 },
    attachTo: document.body, global: { stubs: { CodeEditor: true } } })
  await flushPromises()
  const select = wrapper.findAllComponents(Select).find(item => item.props('mode') === 'multiple')!
  expect(select.props('disabled')).toBe(true)
  expect(select.props('value')).toEqual([101])
  document.querySelector<HTMLButtonElement>('.ant-modal-footer .ant-btn-primary')!.click()
  await flushPromises()
  expect(api.updateTemplate.mock.calls[0][1]).not.toHaveProperty('app_ids')
  wrapper.unmount()
})

it('没有应用读取权限时不允许复制后静默丢失源应用绑定', async () => {
  api.cmdbRead = false
  const wrapper = mount(TicketTemplateEditor, { props: { open: true, templateId: null, copyFromId: 13 },
    attachTo: document.body, global: { stubs: { CodeEditor: true } } })
  await flushPromises()
  document.querySelector<HTMLButtonElement>('.ant-modal-footer .ant-btn-primary')!.click()
  await flushPromises()
  expect(api.createTemplate).not.toHaveBeenCalled()
  await vi.waitFor(() => expect(document.body.textContent).toContain('复制含应用绑定的模板需要 CMDB 查看权限'))
  wrapper.unmount()
})
