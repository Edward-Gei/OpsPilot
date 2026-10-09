// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { config, flushPromises, mount } from '@vue/test-utils'
import Antd, { Select } from 'ant-design-vue'
import AuditLogList from './AuditLogList.vue'

const api = vi.hoisted(() => ({ listAuditLogs: vi.fn(), exportAuditLogs: vi.fn() }))
vi.mock('@/api/audit', () => api)
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: () => true }) }))
const modules = [
  ['auth', '认证'], ['user', '用户'], ['role', '角色'], ['cmdb', '资产'],
  ['credential', '凭据'], ['template', '模板'], ['job', '作业管理'],
  ['ticket', '工单'], ['execution', '执行'], ['notify', '通知'],
  ['system', '系统'], ['audit', '审计'], ['domain', '域名管理'], ['config', '应用配置'],
]
config.global.plugins = [Antd]
class TestResizeObserver { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal('ResizeObserver', TestResizeObserver)
const getComputedStyle = window.getComputedStyle.bind(window)
window.getComputedStyle = ((element: Element) => getComputedStyle(element)) as typeof window.getComputedStyle
window.matchMedia = vi.fn().mockImplementation((media: string) => ({ matches: false, media,
  addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }))
beforeEach(() => {
  vi.clearAllMocks()
  api.listAuditLogs.mockResolvedValue({ total: 14, items: modules.map(([module], index) => ({
    id: index + 1, module, action: 'test.action', result: 'success', created_at: null,
    actor_name: '测试用户', target_type: null, detail: null,
  })) })
})
afterEach(() => { document.body.innerHTML = '' })

it('所有当前和兼容模块在审计列表及筛选选项中显示中文', async () => {
  const wrapper = mount(AuditLogList, { attachTo: document.body })
  await flushPromises()
  const moduleCells = [...document.querySelectorAll('.ant-table-tbody .ant-table-row')].map(row => row.children[3].textContent)
  expect(moduleCells).toEqual(modules.map(([, label]) => label))
  const options = wrapper.findAllComponents(Select).find(select => select.props('placeholder') === '模块')!.props('options')
  expect(options).toHaveLength(14)
  for (const [value, label] of modules) expect(options).toContainEqual({ value, label })
  wrapper.unmount()
})

it.each(['job', 'config', 'domain'])('中文模块筛选 %s 向后端提交原模块编码', async module => {
  const wrapper = mount(AuditLogList, { attachTo: document.body })
  await flushPromises()
  const select = wrapper.findAllComponents(Select).find(select => select.props('placeholder') === '模块')!
  select.vm.$emit('update:value', module)
  select.vm.$emit('change', module)
  await flushPromises()
  expect(api.listAuditLogs).toHaveBeenLastCalledWith(expect.objectContaining({ module, page: 1 }))
  wrapper.unmount()
})

it('审计详情中的模块也使用中文名称', async () => {
  const wrapper = mount(AuditLogList, { attachTo: document.body })
  await flushPromises()
  const row = [...document.querySelectorAll('.ant-table-tbody .ant-table-row')][6]
  row.querySelector<HTMLButtonElement>('button')!.click()
  await flushPromises()
  expect(document.querySelector('.ant-drawer .ant-tag')?.textContent).toBe('作业管理')
  wrapper.unmount()
})
