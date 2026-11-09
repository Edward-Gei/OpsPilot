// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { config, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import Antd, { Select, Table } from 'ant-design-vue'
import dayjs from 'dayjs'
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
const wrappers: VueWrapper[] = []
async function mountAudit() {
  const wrapper = mount(AuditLogList, { attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
function listCalls() {
  return api.listAuditLogs.mock.calls.filter(([params]) => params.page_size !== 1)
}
function lastFilters() { const calls = listCalls(); return calls[calls.length - 1]![0] }
function pagination(wrapper: VueWrapper) {
  return wrapper.getComponent(Table).props('pagination') as { onChange: (page: number, pageSize: number) => void; total: number }
}
function paginate(wrapper: VueWrapper, page = 2) {
  pagination(wrapper).onChange(page, 20)
}
function choose(wrapper: VueWrapper, placeholder: string, value?: string) {
  const select = wrapper.findAllComponents(Select).find(item => item.props('placeholder') === placeholder)!
  select.vm.$emit('update:value', value)
  select.vm.$emit('change', value)
}
config.global.plugins = [Antd]
class TestResizeObserver { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal('ResizeObserver', TestResizeObserver)
const getComputedStyle = window.getComputedStyle.bind(window)
window.getComputedStyle = ((element: Element) => getComputedStyle(element)) as typeof window.getComputedStyle
window.matchMedia = vi.fn().mockImplementation((media: string) => ({ matches: false, media,
  addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }))
beforeEach(() => {
  vi.clearAllMocks()
  api.exportAuditLogs.mockResolvedValue(undefined)
  api.listAuditLogs.mockResolvedValue({ total: 14, items: modules.map(([module], index) => ({
    id: index + 1, module, action: 'test.action', result: 'success', created_at: null,
    actor_name: '测试用户', target_type: null, detail: null,
  })) })
})
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); document.body.innerHTML = '' })

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

it('主工具栏按关键词、模块、结果、单时间范围、更多筛选和导出排列', async () => {
  const wrapper = await mountAudit()
  const toolbar = wrapper.get('.toolbar')
  expect(toolbar.findAll('input[placeholder="操作人"]')).toHaveLength(0)
  expect(toolbar.findAll('input[placeholder="动作（如 login）"]')).toHaveLength(0)
  expect([...toolbar.element.children].map(element =>
    element.querySelector('input')?.getAttribute('placeholder') || element.textContent?.trim(),
  )).toEqual(['搜索审计日志', '模块', '结果', '开始时间', '更多筛选', '导出'])
  expect(toolbar.text()).not.toContain('查询')
  expect(toolbar.text()).not.toContain('重置')
  await toolbar.get('.audit-more-toggle').trigger('click')
  expect(wrapper.find('#audit-advanced input[placeholder="操作人"]').exists()).toBe(true)
  expect(wrapper.find('#audit-advanced input[placeholder="动作（如 login）"]').exists()).toBe(true)
  expect(toolbar.get('.audit-more-toggle').attributes('aria-expanded')).toBe('true')
})

it('默认最近30天，时间快捷选项7/30/90天，保留本地时间不转UTC并覆盖结束秒', async () => {
  const wrapper = await mountAudit()
  const picker = wrapper.getComponent({ name: 'ARangePicker' })
  const initial = lastFilters()
  expect(dayjs(initial.end).diff(dayjs(initial.start), 'day')).toBe(30)
  expect(picker.props('showTime')).toBe(true)
  expect(picker.props('presets').map((preset: { label: string }) => preset.label)).toEqual(['最近 7 天', '最近 30 天', '最近 90 天'])
  const range = ['2026-11-01 01:02:03', '2026-11-02 04:05:06']
  paginate(wrapper)
  await flushPromises()
  const beforePartial = listCalls().length
  picker.vm.$emit('calendarChange', [range[0], null])
  await flushPromises()
  expect(listCalls()).toHaveLength(beforePartial)
  picker.vm.$emit('update:value', range)
  picker.vm.$emit('change', range)
  await flushPromises()
  expect(lastFilters()).toEqual(expect.objectContaining({
    page: 1, start: range[0], end: `${range[1]}.999999`,
  }))
  picker.vm.$emit('update:value', null)
  picker.vm.$emit('change', null)
  await flushPromises()
  expect(lastFilters()).toEqual(expect.objectContaining({ page: 1, start: undefined, end: undefined }))
})

it('关键词回车提交，未提交草稿不随下拉、分页或导出带入，清空立即查询', async () => {
  const wrapper = await mountAudit()
  const keyword = wrapper.get('input[placeholder="搜索审计日志"]')
  await keyword.setValue('  已提交关键词  ')
  expect(listCalls()).toHaveLength(1)
  await keyword.trigger('keydown', { key: 'Enter', keyCode: 13 })
  await flushPromises()
  expect(lastFilters().keyword).toBe('已提交关键词')
  await keyword.setValue('未提交草稿')
  choose(wrapper, '模块', 'config')
  await flushPromises()
  expect(lastFilters()).toEqual(expect.objectContaining({ keyword: '已提交关键词', module: 'config', page: 1 }))
  paginate(wrapper)
  await flushPromises()
  expect(lastFilters()).toEqual(expect.objectContaining({ page: 2, keyword: '已提交关键词' }))
  await (wrapper.vm as unknown as { onExport: (format: 'csv' | 'xlsx') => Promise<void> }).onExport('csv')
  expect(api.exportAuditLogs).toHaveBeenLastCalledWith('csv', expect.objectContaining({ keyword: '已提交关键词', module: 'config' }))
  expect(api.exportAuditLogs.mock.calls[0]![1]).not.toHaveProperty('page')
  await wrapper.get('.toolbar .ant-input-clear-icon').trigger('click')
  await flushPromises()
  expect(lastFilters()).toEqual(expect.objectContaining({ page: 1, keyword: undefined }))
})

it('操作人和动作分别回车提交，其他高级草稿不被附带提交，清空和下拉清除即查', async () => {
  const wrapper = await mountAudit()
  await wrapper.get('.audit-more-toggle').trigger('click')
  const actor = wrapper.get('input[placeholder="操作人"]')
  const action = wrapper.get('input[placeholder="动作（如 login）"]')
  await actor.setValue(' alice ')
  await action.setValue(' login ')
  expect(listCalls()).toHaveLength(1)
  await actor.trigger('keydown', { key: 'Enter', keyCode: 13 })
  await flushPromises()
  expect(lastFilters()).toEqual(expect.objectContaining({ actor: 'alice', action: undefined }))
  await action.trigger('keydown', { key: 'Enter', keyCode: 13 })
  await flushPromises()
  await actor.setValue('bob草稿')
  await action.setValue('delete草稿')
  choose(wrapper, '结果', 'failed')
  await flushPromises()
  expect(lastFilters()).toEqual(expect.objectContaining({ actor: 'alice', action: 'login', result: 'failed' }))
  paginate(wrapper)
  await flushPromises()
  choose(wrapper, '结果')
  await flushPromises()
  expect(lastFilters()).toEqual(expect.objectContaining({ page: 1, result: undefined, actor: 'alice', action: 'login' }))
  await (wrapper.vm as unknown as { onExport: (format: 'csv' | 'xlsx') => Promise<void> }).onExport('xlsx')
  expect(api.exportAuditLogs).toHaveBeenLastCalledWith('xlsx', expect.objectContaining({ actor: 'alice', action: 'login', result: undefined }))
  await actor.element.closest('.ant-input-affix-wrapper')!.querySelector<HTMLSpanElement>('.ant-input-clear-icon')!.click()
  await flushPromises()
  expect(lastFilters()).toEqual(expect.objectContaining({ actor: undefined, action: 'login', page: 1 }))
  await action.element.closest('.ant-input-affix-wrapper')!.querySelector<HTMLSpanElement>('.ant-input-clear-icon')!.click()
  await flushPromises()
  expect(lastFilters().action).toBeUndefined()
})

it.each([true, false])('较早慢响应不能覆盖较新筛选结果或提前结束其加载状态（旧响应先完成=%s）', async oldFirst => {
  const wrapper = await mountAudit()
  let finishOld!: (value: unknown) => void
  let finishNew!: (value: unknown) => void
  api.listAuditLogs.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve }))
  api.listAuditLogs.mockImplementationOnce(() => new Promise(resolve => { finishNew = resolve }))
  choose(wrapper, '模块', 'config')
  choose(wrapper, '模块', 'job')
  await flushPromises()
  const oldResult = { total: 99, items: [{ id: 99, actor_name: '过期结果', module: 'config', result: 'success' }] }
  if (oldFirst) {
    finishOld(oldResult)
    await flushPromises()
    expect(wrapper.getComponent(Table).props('loading')).toBe(true)
    expect(wrapper.text()).not.toContain('过期结果')
  }
  finishNew({ total: 1, items: [{ id: 100, actor_name: '最新结果', module: 'job', result: 'success' }] })
  await flushPromises()
  if (!oldFirst) {
    finishOld(oldResult)
    await flushPromises()
  }
  expect(wrapper.getComponent(Table).props('loading')).toBe(false)
  expect(wrapper.text()).toContain('最新结果')
  expect(wrapper.text()).not.toContain('过期结果')
  expect(pagination(wrapper).total).toBe(1)
})
