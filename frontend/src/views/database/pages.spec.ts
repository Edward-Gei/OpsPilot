// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { compileStyle, parse } from '@vue/compiler-sfc'
import { h, nextTick, reactive } from 'vue'
import dayjs from 'dayjs'
import type { InstanceWrite, TaskRequest, TaskView } from '@/api/database'
import { flushPromises, mount } from '@vue/test-utils'
import Antd, { Modal, Select, Table, message } from 'ant-design-vue'
import { AxiosError, AxiosHeaders, type AxiosAdapter } from 'axios'
import http, { ApiError } from '@/api/http'
import Workbench from './Workbench.vue'
import InstanceList from './InstanceList.vue'
import InstanceEditor from './InstanceEditor.vue'
import Records from './Records.vue'
import ResultTable from './ResultTable.vue'
import TaskStatus from './TaskStatus.vue'
import DatabaseSettingsPanel from './DatabaseSettingsPanel.vue'

const api = vi.hoisted(() => ({
  listInstances: vi.fn(),
  getInstance: vi.fn(),
  getInstanceRoles: vi.fn(),
  updateInstanceRoles: vi.fn(),
  roleOptions: vi.fn(),
  compatibleCredentials: vi.fn(),
  updateInstance: vi.fn(),
  createInstance: vi.fn(),
  clientPolicy: vi.fn(),
  submitTask: vi.fn(),
  getTask: vi.fn(),
  getResult: vi.fn(),
  cancelTask: vi.fn(),
  listTasks: vi.fn(),
  listAudit: vi.fn(),
  getAudit: vi.fn(),
  deleteInstance: vi.fn(),
  importInstances: vi.fn(), downloadInstanceImportTemplate: vi.fn(), exportInstances: vi.fn()
}))
const state = vi.hoisted(() => ({
  permissions: new Set<string>(),
  query: {} as Record<string, string>,
  route: { path: '/database/instances', query: undefined as Record<string, string> | undefined }
}))
const sys = vi.hoisted(() => ({ updateConfigs: vi.fn() }))
const navigation = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))
const cmdb = vi.hoisted(() => ({ listApps: vi.fn(), listHosts: vi.fn() }))
vi.mock('@/api/database', () => api)
vi.mock('@/api/cmdb', () => cmdb)
vi.mock('@/api/system', () => sys)
vi.mock('@/stores/user', () => ({
  useUserStore: () => ({
    userInfo: null,
    hasPerm: (p: string) => state.permissions.has(p),
    hasAnyPerm: (ps: string[]) => ps.some(p => state.permissions.has(p))
  })
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({
    get query() { return state.route.query || state.query },
    get params() {
      const id = state.route.path.match(/^\/database\/instances\/(\d+)/)?.[1]
      return id ? { id } : {}
    },
    get path() { return state.route.path }
  }),
  useRouter: () => navigation
}))
const instance = {
  id: 1,
  name: '共享MySQL',
  description: null,
  engine: 'mysql',
  business_line: 'mitrade',
  cluster_name: null,
  cluster_role: null,
  version: null,
  enabled: true,
  needs_configuration: false,
  created_at: null,
  updated_at: null,
  apps: [],
  hosts: [],
  last_probe_status: 'untested',
  last_probe_message: null,
  last_probe_at: null,
  environment: 'prod',
  host: 'mysql.example.test',
  port: 3306,
  credential_id: 1,
  credential: { id: 1, name: '只读', login_user: 'ro', auth_type: 'username_password' },
  connection_options: { tls: false, tls_verify: true, default_database: 'sales' },
  config_revision: 1,
  app_ids: [],
  host_ids: []
}
const task = {
  task_id: 31,
  kind: 'sql',
  status: 'queued',
  instance_id: 1,
  instance_name: instance.name,
  engine: 'mysql',
  context: { database: 'sales' },
  submitted_text: 'SELECT 1;',
  executed_statement: 'SELECT 1',
  operation_summary: '只读查询',
  created_at: '2026-01-01T00:00:00Z',
  started_at: null,
  finished_at: null,
  expires_at: null,
  stop_reason: null,
  error_code: null,
  error_message: null,
  stop_confirmation: null,
  result_summary: null,
  policy_snapshot: null
}
const result = {
  columns: [{ ordinal: 0, name: 'id', type: 'int' }, { ordinal: 1, name: 'id', type: 'text' }],
  rows: [[{ $type: 'int', value: '9007199254740993' }, '<b>原始数据</b>']],
  returned_rows: 1,
  returned_bytes: 100,
  truncated: false,
  truncate_reason: null,
  expires_at: '2099-01-01T00:00:00Z',
  saved_total: 1,
  page: 1,
  page_size: 50
}
const auditRecord = {
  ...task, id: 101, actor_id: 8, actor_name: '数据库审计对象', source_ip: '192.0.2.18', status: 'succeeded' as const,
  operation_summary: '审计查询摘要', finished_at: '2026-01-01T00:00:01Z',
  target_snapshot: { name: '目标快照', host: 'database.internal' },
  credential_snapshot: { id: 7, name: '只读凭据快照', login_user: 'report_reader' },
  result_summary: { returned_rows: 9, returned_bytes: 128, truncated: false },
  events: [{ id: 11, event_type: 'stopped', created_at: '2026-01-01T00:00:01Z', error_code: 'AUDIT_EVENT_CODE', error_message: '审计事件说明',
    validation: { rule: '只读校验事实' }, stop_confirmation: { confirmed: true, source: 'driver' }, result_summary: { returned_rows: 9 } }]
}
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)
window.matchMedia = vi.fn().mockImplementation((media: string) => ({
  matches: false,
  media,
  onchange: null,
  addListener: vi.fn(),
  removeListener: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  dispatchEvent: vi.fn()
}))
const actualStyle = window.getComputedStyle.bind(window)
window.getComputedStyle = ((element: Element) => actualStyle(element)) as typeof window.getComputedStyle
const themeTokens = readFileSync('src/styles/tokens.css', 'utf8')
const options = {
  global: {
    plugins: [Antd],
    stubs: {
      CodeEditor: {
        props: ['modelValue'],
        template: '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />'
      }
    }
  }
}
const wrappers: ReturnType<typeof mount>[] = []
const originalHttpAdapter = http.defaults.adapter
const linkedInstances = [
  { ...instance, id: 31, name: '共享实例' },
  { ...instance, id: 32, name: '共享实例扩展' },
]
beforeEach(() => {
  vi.clearAllMocks()
  for (const method of Object.values(api)) method.mockReset()
  state.permissions = new Set(['database:query', 'database:write', 'database:read', 'database:audit'])
  state.query = {}
  state.route = reactive({ path: '/database/instances', query: undefined as Record<string, string> | undefined })
  navigation.replace.mockImplementation(async ({ path }: { path: string }) => {
    state.route.path = path
    state.route.query = {}
  })
  api.listInstances.mockResolvedValue({ items: [instance], total: 1, page: 1, page_size: 100 })
  api.getInstance.mockResolvedValue(instance)
  api.getInstanceRoles.mockResolvedValue({ items: [] })
  api.updateInstanceRoles.mockResolvedValue(null)
  api.roleOptions.mockResolvedValue({ items: [] })
  api.compatibleCredentials.mockResolvedValue({ items: [instance.credential] })
  api.updateInstance.mockResolvedValue(instance)
  api.createInstance.mockResolvedValue(instance)
  api.importInstances.mockResolvedValue({ success_count: 1, created_count: 1, updated_count: 0, failed_rows: [] })
  api.downloadInstanceImportTemplate.mockResolvedValue(undefined)
  api.exportInstances.mockResolvedValue(undefined)
  cmdb.listApps.mockResolvedValue({ items: [], total: 0 })
  cmdb.listHosts.mockResolvedValue({ items: [], total: 0 })
  api.clientPolicy.mockResolvedValue({
    execution_timeout_seconds: 60,
    max_result_rows: 1000,
    max_result_bytes: 5242880,
    result_ttl_minutes: 30
  })
  api.submitTask.mockImplementation(async req => ({
    task_id: 31,
    status: 'queued',
    executed_statement: req.kind === 'sql' ? 'SELECT 2' : null,
    operation_summary: '已受理'
  }))
  api.getTask.mockResolvedValue(task)
  api.getResult.mockResolvedValue(result)
  api.listAudit.mockResolvedValue({ items: [], total: 0 })
  api.listTasks.mockResolvedValue({ items: [], total: 0 })
  sys.updateConfigs.mockResolvedValue(null)
})
afterEach(() => {
  for (const wrapper of wrappers.splice(0)) wrapper.unmount()
  http.defaults.adapter = originalHttpAdapter
  message.destroy()
  for (const method of [message.success, message.error, message.warning]) {
    if (vi.isMockFunction(method)) method.mockRestore()
  }
  if (vi.isMockFunction(Modal.confirm)) vi.mocked(Modal.confirm).mockRestore()
  vi.useRealTimers()
  document.body.innerHTML = ''
  document.documentElement.scrollTop = 0
})
it.each([
  { name: '实例台账', component: InstanceList, props: {} },
  { name: 'SQL工作台', component: Workbench, props: {} },
  { name: '本人历史', component: Records, props: { audit: false } },
  { name: '执行审计', component: Records, props: { audit: true } },
])('$name标题白字必须配套实际主题背景，避免落到浅色页面底色', async ({ component, props }) => {
  const style = document.createElement('style')
  style.textContent = themeTokens
  document.head.append(style)
  try {
    const wrapper = mount({ render: () => h(component, props) }, options)
    wrappers.push(wrapper)
    await flushPromises()
    const hero = wrapper.get('.op-hero').element
    const backgroundRules = Array.from(style.sheet!.cssRules)
      .filter((rule): rule is CSSStyleRule => 'selectorText' in rule && 'style' in rule)
      .filter(rule => hero.matches(rule.selectorText))
      .map(rule => rule.style.getPropertyValue('background'))
      .filter(Boolean)
    expect(backgroundRules.some(value => value.includes('linear-gradient'))).toBe(true)
  } finally {
    style.remove()
  }
})
it('数据库页面进入与同组件路径切换复位外层滚动，筛选参数变化保留位置', async () => {
  document.documentElement.scrollTop = 640
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  expect(document.documentElement.scrollTop).toBe(0)
  document.documentElement.scrollTop = 320
  state.query = { keyword: 'mysql' }
  await flushPromises()
  expect(document.documentElement.scrollTop).toBe(320)
  state.route.path = '/database/instances/2'
  await flushPromises()
  expect(document.documentElement.scrollTop).toBe(0)
})

it.each([
  { permissions: ['database:read'], imported: false, exported: true },
  { permissions: ['database:write'], imported: false, exported: false },
  { permissions: ['database:import'], imported: false, exported: false },
  { permissions: ['database:read', 'database:import'], imported: false, exported: true },
  { permissions: ['database:write', 'database:import'], imported: true, exported: false },
  { permissions: ['database:read', 'database:write', 'database:import', 'database:delete'], imported: true, exported: true },
])('Excel 权限组合 $permissions 严格限制模板、导入与导出', async ({ permissions, imported, exported }) => {
  state.permissions = new Set(permissions)
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  const actions = wrapper.get('.instance-actions').findAll('button').map(button => button.text().replace(/\s/g, ''))
  expect(actions.includes('导入')).toBe(imported)
  expect(actions.includes('导出')).toBe(exported)
  if (imported && exported) expect(actions).toEqual(['批量删除（0）', '导出', '导入', '新建实例'])
  const vm = wrapper.vm as unknown as { openImport: () => void; downloadTemplate: () => Promise<void>; exportCurrent: () => Promise<void> }
  vm.openImport()
  await vm.downloadTemplate()
  await vm.exportCurrent()
  await flushPromises()
  expect(document.querySelector('.instance-import-modal') !== null).toBe(imported)
  expect(api.downloadInstanceImportTemplate).toHaveBeenCalledTimes(imported ? 1 : 0)
  expect(api.exportInstances).toHaveBeenCalledTimes(exported ? 1 : 0)
})

it('Excel 导入展示十列说明、下载模板、upsert 与失败行明细，完成后刷新列表', async () => {
  state.permissions.add('database:import')
  api.importInstances.mockResolvedValue({ success_count: 3, created_count: 2, updated_count: 1, failed_rows: [{ row: 5, reason: '端口必须为整数' }] })
  const wrapper = mount(InstanceList, { ...options, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  await wrapper.get('.instance-actions').findAll('button').find(button => button.text() === '导入')!.trigger('click')
  await flushPromises()
  const modal = () => document.querySelector('.instance-import-modal')!
  expect(modal().textContent).toContain('实例名称、引擎、连接地址、端口、环境、描述、业务线、集群名称、集群角色、版本')
  expect(modal().textContent).toContain('5000')
  const template = [...modal().querySelectorAll('a')].find(link => link.textContent === '下载导入模板')!
  template.click()
  await flushPromises()
  expect(api.downloadInstanceImportTemplate).toHaveBeenCalledOnce()
  const file = new File(['xlsx'], '实例.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
  const input = modal().querySelector<HTMLInputElement>('input[type=file]')!
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  input.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
  const checkbox = modal().querySelector<HTMLInputElement>('input[type=checkbox]')!
  checkbox.click()
  await flushPromises()
  const submit = [...modal().querySelectorAll<HTMLButtonElement>('.ant-modal-footer button')].find(button => button.textContent?.replace(/\s/g, '') === '开始导入')!
  submit.click()
  await flushPromises()
  expect(api.importInstances).toHaveBeenCalledWith(file, true)
  expect(modal().textContent).toContain('新增 2 条')
  expect(modal().textContent).toContain('更新 1 条')
  expect(modal().querySelector('tbody')?.textContent).toContain('5端口必须为整数')
  expect(api.listInstances).toHaveBeenCalledTimes(2)
})

it('Excel 导入拒绝非xlsx或超10MiB文件，并在提交期间阻止重复上传或关闭', async () => {
  state.permissions.add('database:import')
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as { importVisible: boolean; importFile: File | null; importLoading: boolean; openImport: () => void; beforeImportUpload: (file: File) => boolean; submitImport: () => Promise<void> }
  vm.openImport()
  vm.beforeImportUpload(new File(['csv'], '实例.csv'))
  expect(vm.importFile).toBeNull()
  const large = new File(['large'], 'large.xlsx')
  Object.defineProperty(large, 'size', { value: 10 * 1024 * 1024 + 1 })
  vm.beforeImportUpload(large)
  expect(vm.importFile).toBeNull()
  const valid = new File(['xlsx'], '实例.xlsx')
  vm.beforeImportUpload(valid)
  let resolve!: (value: unknown) => void
  api.importInstances.mockImplementationOnce(() => new Promise(done => { resolve = done }))
  const pending = vm.submitImport()
  await flushPromises()
  expect(vm.importLoading).toBe(true)
  const modal = wrapper.findAllComponents(Modal).find(component => component.props('title') === 'Excel 导入数据库实例')!
  expect(modal.props()).toMatchObject({ closable: false, maskClosable: false, keyboard: false, cancelButtonProps: { disabled: true } })
  await vm.submitImport()
  vm.beforeImportUpload(new File(['other'], 'other.xlsx'))
  expect(vm.importFile).toBe(valid)
  expect(api.importInstances).toHaveBeenCalledOnce()
  resolve({ success_count: 1, created_count: 1, updated_count: 0, failed_rows: [] })
  await pending
  await vm.submitImport()
  expect(api.importInstances).toHaveBeenCalledOnce()
})

it('Excel 全量导出仅采用已应用筛选和排序，忽略草稿关键词与分页', async () => {
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as { keyword: string; query: Record<string, unknown>; search: () => void; exportCurrent: () => Promise<void> }
  vm.keyword = '已提交'
  vm.search()
  await flushPromises()
  vm.keyword = '未提交草稿'
  Object.assign(vm.query, { page: 4, page_size: 50, engine: 'mysql', business_line: 'mitrade', enabled: false, cluster_name: 'prod', sort_by: 'version', sort_order: 'desc' })
  await vm.exportCurrent()
  expect(api.exportInstances).toHaveBeenCalledWith({ scope: 'management', keyword: '已提交', engine: 'mysql', business_line: 'mitrade', enabled: false, cluster_name: 'prod', sort_by: 'version', sort_order: 'desc', instance_id: undefined, app_id: undefined, host_id: undefined })
})

it('Excel 导出保留关联精确ID，不把回显名称用作模糊词', async () => {
  state.query = { instance_id: '1', app_id: '7', host_id: '9', related: 'app' }
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  await (wrapper.vm as unknown as { exportCurrent: () => Promise<void> }).exportCurrent()
  expect(api.exportInstances).toHaveBeenCalledWith(expect.objectContaining({ instance_id: 1, app_id: 7, host_id: 9, keyword: undefined }))
})

it('待配置实例显示状态、禁用测试与查询，详情可安全显示未绑定凭据', async () => {
  const draft = { ...instance, enabled: false, credential_id: null, credential: null, needs_configuration: true }
  api.listInstances.mockResolvedValue({ items: [draft], total: 1 })
  api.getInstance.mockResolvedValue(draft)
  state.route.path = '/database/instances/1'
  const wrapper = mount(InstanceList, { ...options, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  expect(wrapper.get('tbody').text()).toContain('待配置')
  const test = wrapper.get('tbody').findAll('button').find(button => button.text().replace(/\s/g, '') === '测试')!
  expect(test.attributes('disabled')).toBeDefined()
  await (wrapper.vm as unknown as { testInstance: (id: number) => Promise<void> }).testInstance(1)
  expect(api.submitTask).not.toHaveBeenCalled()
  expect(wrapper.get('tbody').text()).not.toContain('查询')
  expect(document.querySelector('.ant-drawer')?.textContent).toContain('未绑定只读凭据')
})

it('待配置PostgreSQL草稿允许保存空凭据与默认库，启用时要求补齐凭据', async () => {
  const draft = { ...instance, engine: 'postgresql', enabled: false, credential_id: null, credential: null, needs_configuration: true, connection_options: { tls: false, tls_verify: true } }
  api.getInstance.mockResolvedValue(draft)
  const wrapper = mount(InstanceEditor, { ...options, props: { instanceId: 1 }, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as { step: number; form: InstanceWrite; next: () => Promise<void>; save: () => Promise<void> }
  vm.form.description = '补充草稿台账'
  await vm.next()
  expect(vm.step).toBe(1)
  await vm.save()
  expect(api.updateInstance).toHaveBeenCalledWith(1, expect.objectContaining({ enabled: false, credential_id: null }))
})

it('编辑待配置实例启用前要求凭据，补齐后可保存并保留原授权流程', async () => {
  api.getInstance.mockResolvedValue({ ...instance, enabled: false, credential_id: null, credential: null, needs_configuration: true })
  const wrapper = mount(InstanceEditor, { ...options, props: { instanceId: 1 } })
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as { step: number; form: InstanceWrite; next: () => Promise<void>; save: () => Promise<void> }
  vm.form.enabled = true
  await vm.next()
  expect(vm.step).toBe(0)
  expect(api.updateInstance).not.toHaveBeenCalled()
  vm.form.credential_id = 1
  await vm.next()
  expect(vm.step).toBe(1)
  await vm.save()
  expect(api.updateInstance).toHaveBeenCalledWith(1, expect.objectContaining({ credential_id: 1, enabled: true }))
})

it('工作台排除停用或待配置实例，不为其读取结构或执行查询', async () => {
  api.listInstances.mockResolvedValue({ items: [{ ...instance, needs_configuration: true }, { ...instance, id: 2, enabled: false }], total: 2 })
  state.query = { instance_id: '1' }
  const wrapper = mount(Workbench, options)
  wrappers.push(wrapper)
  await flushPromises()
  const select = wrapper.findComponent(Select)
  expect(select.props('options')).toEqual([])
  expect(api.getInstance).not.toHaveBeenCalled()
  expect(api.submitTask).not.toHaveBeenCalled()
})
it.each(['app', 'host'])('进入或刷新%s关联实例链接时回显名称，仍只按精确ID查询', async related => {
  state.query = { instance_id: '31', related }
  api.listInstances.mockImplementation(async ({ instance_id }: { instance_id?: number }) => ({
    items: instance_id ? linkedInstances.filter(item => item.id === instance_id) : linkedInstances,
    total: instance_id ? 1 : 2,
  }))
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  const input = wrapper.get<HTMLInputElement>('input[placeholder="搜索实例信息"]')
  expect(input.element.value).toBe('共享实例')
  expect(api.listInstances).toHaveBeenLastCalledWith(expect.objectContaining({ instance_id: 31, keyword: undefined }))
  expect(wrapper.findAll('tbody tr[data-row-key]')).toHaveLength(1)
  expect(wrapper.find('.ant-drawer').exists()).toBe(false)
})
it('实例关联名称未修改时按回车保留ID，不把显示名称变为模糊条件', async () => {
  state.query = { instance_id: '31', related: 'app' }
  api.listInstances.mockResolvedValue({ items: [linkedInstances[0]], total: 1 })
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  const input = wrapper.get<HTMLInputElement>('input[placeholder="搜索实例信息"]')
  expect(input.element.value).toBe('共享实例')
  await input.trigger('keydown.enter')
  await flushPromises()
  expect(api.listInstances).toHaveBeenLastCalledWith(expect.objectContaining({ instance_id: 31, keyword: undefined }))
})
it('同组件快速切换关联实例时，旧列表响应不覆盖新目标名称和结果', async () => {
  state.query = { instance_id: '31', related: 'app' }
  let finishFirst!: (value: { items: typeof linkedInstances; total: number }) => void
  api.listInstances.mockImplementation(async ({ instance_id }: { instance_id?: number }) => instance_id === 31
    ? new Promise(resolve => { finishFirst = resolve })
    : { items: [linkedInstances[1]], total: 1 })
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  state.route.query = { instance_id: '32', related: 'host' }
  await flushPromises()
  const input = wrapper.get<HTMLInputElement>('input[placeholder="搜索实例信息"]')
  expect(input.element.value).toBe('共享实例扩展')
  expect(api.listInstances).toHaveBeenLastCalledWith(expect.objectContaining({ instance_id: 32, keyword: undefined }))
  finishFirst({ items: [linkedInstances[0]], total: 1 })
  await flushPromises()
  expect(input.element.value).toBe('共享实例扩展')
  expect(wrapper.findAll('tbody tr[data-row-key]').map(row => row.attributes('data-row-key'))).toEqual(['32'])
})
it.each(['其他实例', ''])('修改关联实例搜索为“%s”解除精确ID，迟到响应不恢复旧名称', async keyword => {
  state.query = { instance_id: '31', related: 'app' }
  api.listInstances.mockResolvedValue({ items: [linkedInstances[0]], total: 1 })
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  const input = wrapper.get<HTMLInputElement>('input[placeholder="搜索实例信息"]')
  expect(input.element.value).toBe('共享实例')
  let finishOld!: (value: { items: typeof linkedInstances; total: number }) => void
  const other = { ...instance, id: 99, name: '其他实例' }
  api.listInstances.mockImplementation(async ({ instance_id }: { instance_id?: number }) => instance_id === 31
    ? new Promise(resolve => { finishOld = resolve })
    : { items: [other], total: 1 })
  await input.trigger('keydown.enter')
  await flushPromises()
  await input.setValue(keyword)
  await flushPromises()
  expect(api.listInstances).toHaveBeenLastCalledWith(expect.objectContaining({ instance_id: undefined, keyword: keyword || undefined }))
  finishOld({ items: [linkedInstances[0]], total: 1 })
  await flushPromises()
  expect(input.element.value).toBe(keyword)
  expect(wrapper.findAll('tbody tr[data-row-key]').map(row => row.attributes('data-row-key'))).toEqual(['99'])
})
it('兼具管理权限的工作台仍只取query范围，完整提交原SQL，受理时不显示已完成', async () => {
  const wrapper = mount(Workbench, options)
  wrappers.push(wrapper)
  await flushPromises()
  const page = wrapper.vm as unknown as {
    instanceId: number
    chooseInstance: () => Promise<void>
    sql: string
    execute: () => Promise<void>
  }
  page.instanceId = 1
  await page.chooseInstance()
  await flushPromises()
  api.submitTask.mockClear()
  const text = "SELECT ';' AS text;\nSELECT 2;"
  page.sql = text
  await page.execute()
  await flushPromises()
  expect(api.listInstances.mock.calls.every(([params]) => params.scope === 'query')).toBe(true)
  expect(api.submitTask).toHaveBeenCalledWith({ kind: 'sql', instance_id: 1, context: { database: 'sales' }, sql: text }, expect.any(AbortSignal))
  expect(wrapper.text()).toContain('排队中')
  expect(wrapper.text()).not.toContain('已完成')
  expect(api.getResult).not.toHaveBeenCalled()
})
it('SQL工作台使用统一蓝色页头，选择实例后保留最近测试状态', async () => {
  mockWorkbenchData()
  api.getInstance.mockResolvedValue({ ...instance, last_probe_status: 'succeeded' })
  const wrapper = mount(Workbench, options)
  wrappers.push(wrapper)
  await flushPromises()
  expect(wrapper.get('h1').text()).toBe('SQL工作台')
  expect(wrapper.get('.op-hero').classes()).toContain('op-hero--blue')
  expect(wrapper.text()).not.toContain('数据库连接可用')
  expect(wrapper.text()).toContain('选择实例')
  const page = wrapper.vm as unknown as { instanceId: number; chooseInstance: () => Promise<void> }
  page.instanceId = 1
  await page.chooseInstance()
  await flushPromises()
  expect(wrapper.get('.op-hero').text()).toContain('最近测试通过')
})

function mockWorkbenchData() {
  const requests = new Map<number, TaskRequest>()
  api.submitTask.mockImplementation(async (request: TaskRequest) => {
    const task_id = 100 + requests.size
    requests.set(task_id, request)
    return { task_id, status: 'queued', executed_statement: null, operation_summary: '已受理' }
  })
  api.getTask.mockImplementation(async (id: number) => {
    const request = requests.get(id)!
    return { ...task, ...request, task_id: id, status: 'succeeded',
      operation_summary: request.kind === 'metadata' ? `元数据：${request.operation}` : '只读查询' }
  })
  api.getResult.mockImplementation(async (id: number, page = 1, page_size = 50) => {
    const request = requests.get(id)!
    if (request.kind !== 'metadata') return { ...result, page, page_size, rows: [[42, 'fresh-value']] }
    const rows: Record<string, unknown[][]> = {
      databases: [['sales'], ['archive']], schemas: [['public'], ['audit']],
      tables: request.context.database === 'archive' ? [['ledger']] : [['orders'], ['customers']],
      collections: [['events']], columns: [['order_id', 'bigint', 'NO'], ['payload', 'json', 'YES']],
      fields: [['customer', 'string', true]]
    }
    const structure = ['columns', 'fields'].includes(request.operation)
    return { ...result, page, page_size,
      columns: structure
        ? [{ ordinal: 0, name: 'name', type: 'string' }, { ordinal: 1, name: 'type', type: 'string' },
          { ordinal: 2, name: request.operation === 'fields' ? 'inferred' : 'nullable', type: 'string' }]
        : [{ ordinal: 0, name: 'name', type: 'string' }],
      rows: rows[request.operation] || [], saved_total: rows[request.operation]?.length || 0
    }
  })
  return requests
}

async function mountConnectedWorkbench(attachTo?: Element) {
  const wrapper = mount(Workbench, { ...options, attachTo })
  wrappers.push(wrapper)
  await flushPromises()
  const page = wrapper.vm as unknown as {
    instanceId: number; chooseInstance: () => Promise<void>; chooseObject: (name: string) => void
    sql: string; database: string; schema: string; execute: () => Promise<void>
    databaseChanged: () => void; schemaChanged: () => void; loadContext: () => Promise<void>
    mongo: Record<string, unknown>; redis: Record<string, unknown>
  }
  page.instanceId = 1
  await page.chooseInstance()
  await flushPromises()
  return { wrapper, page }
}

const workbenchTab = (wrapper: ReturnType<typeof mount>, label: string) =>
  wrapper.findAll('[role="tab"]').find(tab => tab.text().startsWith(label))!
const workbenchCommand = (wrapper: ReturnType<typeof mount>, label: string) =>
  wrapper.findAll('button').find(button => button.text().replace(/\s/g, '') === label.replace(/\s/g, ''))!
const loadingCommands = (wrapper: ReturnType<typeof mount>) =>
  wrapper.findAll('button.ant-btn-loading').map(button => button.text().replace(/\s/g, ''))

it.each([
  { engine: 'mysql', restoredDatabase: 'sales', delayed: false },
  { engine: 'mysql', restoredDatabase: 'sales', delayed: true },
  { engine: 'mysql', restoredDatabase: 'archive', delayed: false },
  { engine: 'mysql', restoredDatabase: 'archive', delayed: true },
  { engine: 'postgresql', restoredDatabase: 'archive', delayed: false },
  { engine: 'postgresql', restoredDatabase: 'archive', delayed: true },
  { engine: 'clickhouse', restoredDatabase: 'archive', delayed: false },
  { engine: 'clickhouse', restoredDatabase: 'archive', delayed: true },
])('工作台历史恢复 $engine/$restoredDatabase（库任务延迟=$delayed）保留真实库根节点和可见对象，不执行原SQL', async ({ engine, restoredDatabase, delayed }) => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const requests = mockWorkbenchData()
  api.getInstance.mockResolvedValue({ ...instance, engine, connection_options: { default_database: 'sales', default_schema: 'public' } })
  const submitted = 'SELECT * FROM restored_history_table;'
  const context = { database: restoredDatabase, ...(engine === 'postgresql' ? { schema: 'audit' } : {}) }
  const getTask = api.getTask.getMockImplementation()!
  let databaseFinished = !delayed
  api.getTask.mockImplementation(async id => {
    if (id === 31) return { ...task, engine, context, status: 'succeeded', submitted_text: submitted }
    const current = await getTask(id)
    const request = requests.get(id)!
    return { ...current, status: request.kind === 'metadata' && request.operation === 'databases' && !databaseFinished ? 'running' : 'succeeded' }
  })
  state.route.path = '/database/workbench'
  state.route.query = { task_id: '31' }
  const wrapper = mount(Workbench, options)
  wrappers.push(wrapper)
  await flushPromises()
  type BrowserEntry = { title: string; children?: BrowserEntry[] }
  const page = wrapper.vm as unknown as { instanceId: number; database: string; schema: string; sql: string; dbOptions: string[]; objects: string[]; browserTree: BrowserEntry[] }
  expect(page.instanceId).toBe(1)
  expect(page.database).toBe(restoredDatabase)
  expect(page.sql).toBe(submitted)
  if (engine === 'postgresql') expect(page.schema).toBe('audit')
  databaseFinished = true
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  const expectedObjects = restoredDatabase === 'archive' ? ['ledger'] : ['orders', 'customers']
  const flatten = (nodes: BrowserEntry[]): string[] => nodes.flatMap(node => [node.title, ...flatten(node.children || [])])
  expect.soft(page.dbOptions).toEqual(['sales', 'archive'])
  expect.soft(page.objects).toEqual(expectedObjects)
  expect.soft(page.browserTree.map(node => node.title)).toEqual(['sales', 'archive'])
  expect.soft(flatten(page.browserTree)).toEqual(expect.arrayContaining([restoredDatabase, ...expectedObjects]))
  for (const name of expectedObjects) expect.soft(wrapper.get('.db-workspace-browser').text()).toContain(name)
  const metadataRequests = [...requests.values()].filter(request => request.kind === 'metadata')
  expect.soft(metadataRequests.map(request => request.operation)).toEqual(engine === 'postgresql' ? ['databases', 'schemas', 'tables'] : ['databases', 'tables'])
  expect.soft(metadataRequests.every(request => request.context.database === restoredDatabase)).toBe(true)
  if (engine === 'postgresql') expect.soft(metadataRequests.every(request => request.context.schema === 'audit')).toBe(true)
  expect([...requests.values()].every(request => request.kind === 'metadata')).toBe(true)
  expect(api.getResult.mock.calls.every(([id]) => id !== 31)).toBe(true)
})

it('工作台历史恢复不读取未授权历史实例的元数据或执行原SQL', async () => {
  mockWorkbenchData()
  api.getTask.mockResolvedValue({ ...task, instance_id: 2, status: 'succeeded' })
  state.query = { task_id: '31' }
  const wrapper = mount(Workbench, options)
  wrappers.push(wrapper)
  await flushPromises()
  expect(api.getInstance).not.toHaveBeenCalled()
  expect(api.submitTask).not.toHaveBeenCalled()
  expect(wrapper.find('.db-workspace-browser').exists()).toBe(false)
})

it('工作台历史恢复后普通实例切换使用新实例默认库，不沿用历史上下文', async () => {
  const requests = mockWorkbenchData()
  api.listInstances.mockResolvedValue({ items: [instance, { ...instance, id: 2 }], total: 2 })
  const getTask = api.getTask.getMockImplementation()!
  api.getTask.mockImplementation(id => id === 31 ? Promise.resolve({ ...task, status: 'succeeded', context: { database: 'archive' } }) : getTask(id))
  state.query = { task_id: '31' }
  const wrapper = mount(Workbench, options)
  wrappers.push(wrapper)
  await flushPromises()
  const page = wrapper.vm as unknown as { database: string; objects: string[] }
  expect(page.database).toBe('archive')
  expect(page.objects).toEqual(['ledger'])
  api.getInstance.mockResolvedValue({ ...instance, id: 2 })
  const select = wrapper.get('.db-workspace-instance').getComponent(Select)
  select.vm.$emit('update:value', 2)
  select.vm.$emit('change', 2)
  await flushPromises()
  expect(page.database).toBe('sales')
  expect(page.objects).toEqual(['orders', 'customers'])
  expect(wrapper.get('.db-workspace-browser').text()).toContain('orders')
  expect(wrapper.get('.db-workspace-browser').text()).not.toContain('ledger')
  expect([...requests.values()].every(request => request.kind === 'metadata')).toBe(true)
})

it('工作台历史恢复的PostgreSQL Schema不可见时只向实际可见Schema读取表', async () => {
  const requests = mockWorkbenchData()
  api.getInstance.mockResolvedValue({ ...instance, engine: 'postgresql', connection_options: { default_database: 'sales', default_schema: 'public' } })
  const getTask = api.getTask.getMockImplementation()!
  api.getTask.mockImplementation(id => id === 31 ? Promise.resolve({ ...task, engine: 'postgresql', status: 'succeeded', context: { database: 'archive', schema: 'hidden_schema' } }) : getTask(id))
  const getResult = api.getResult.getMockImplementation()!
  api.getResult.mockImplementation(async (...args) => {
    const request = requests.get(args[0])!
    const data = await getResult(...args)
    return request.kind === 'metadata' && request.operation === 'schemas' ? { ...data, rows: [['audit']] } : data
  })
  state.query = { task_id: '31' }
  const wrapper = mount(Workbench, options)
  wrappers.push(wrapper)
  await flushPromises()
  expect((wrapper.vm as unknown as { schema: string }).schema).toBe('audit')
  expect([...requests.values()].flatMap(request => request.kind === 'metadata' && request.operation === 'tables' ? [request.context.schema] : [])).toEqual(['audit'])
  expect(wrapper.get('.db-workspace-browser').text()).toContain('ledger')
  expect(wrapper.get('.db-workspace-browser').text()).not.toContain('hidden_schema')
})

it.each([
  { engine: 'mysql', object: 'orders', operation: 'columns' },
  { engine: 'mongodb', object: 'events', operation: 'fields' },
])('工作台交互选择$engine对象读取字段时仅浏览器刷新按钮显示单spinner', async ({ engine, object, operation }) => {
  const requests = mockWorkbenchData()
  api.getInstance.mockResolvedValue({ ...instance, engine })
  const { wrapper } = await mountConnectedWorkbench()
  const getResult = api.getResult.getMockImplementation()!
  let finish!: (value: typeof result) => void
  let fields!: typeof result
  api.getResult.mockImplementation(async (...args) => {
    const data = await getResult(...args)
    const request = requests.get(args[0])!
    if (request.kind === 'metadata' && request.operation === operation) {
      fields = data
      return new Promise(resolve => { finish = resolve })
    }
    return data
  })
  await wrapper.findAll('.ant-tree-title').find(node => node.text() === object)!.trigger('click')
  await flushPromises()
  const refresh = wrapper.get('button[aria-label="刷新可见库"]')
  expect(loadingCommands(wrapper)).toEqual([''])
  expect(refresh.findAll('.anticon-loading')).toHaveLength(1)
  expect(refresh.find('.anticon-reload').exists()).toBe(false)
  expect(workbenchCommand(wrapper, '刷新结构').classes()).not.toContain('ant-btn-loading')
  finish(fields)
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual([])
  expect(refresh.findAll('.anticon-reload')).toHaveLength(1)
})

it('工作台交互分页保留当前结果表并显示Ant加载罩，完成后替换当前页', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  mockWorkbenchData()
  const { wrapper, page } = await mountConnectedWorkbench()
  await page.execute()
  await flushPromises()
  const current = wrapper.getComponent(ResultTable)
  let finish!: (value: typeof result) => void
  api.getResult.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  current.vm.$emit('page', 2, 20)
  await flushPromises()
  await vi.advanceTimersByTimeAsync(1)
  await flushPromises()
  expect(wrapper.findComponent(ResultTable).exists()).toBe(true)
  expect(wrapper.getComponent(ResultTable).element).toBe(current.element)
  expect(current.props('loading')).toBe(true)
  expect(wrapper.get('.db-workspace-result').text()).toContain('fresh-value')
  expect(wrapper.find('.db-workspace-result .ant-spin-spinning').exists()).toBe(true)
  finish({ ...result, page: 2, page_size: 20, rows: [['2', 'page-two']] })
  await flushPromises()
  await vi.advanceTimersByTimeAsync(1)
  await flushPromises()
  expect(wrapper.getComponent(ResultTable).props('loading')).toBe(false)
  expect(wrapper.find('.db-workspace-result .ant-spin-spinning').exists()).toBe(false)
  expect(wrapper.get('.db-workspace-result').text()).toContain('page-two')
  expect(wrapper.get('.db-workspace-result').text()).not.toContain('fresh-value')
})

it('工作台交互桌面卡片固定剩余高度，内容内部滚动且SQL编辑器可垂直调高', async () => {
  mockWorkbenchData()
  const { wrapper } = await mountConnectedWorkbench(document.body)
  const { descriptor } = parse(readFileSync('src/views/database/Workbench.vue', 'utf8'))
  const scopeId = (wrapper.element as Element).getAttributeNames().find(name => name.startsWith('data-v-'))!
  const style = document.createElement('style')
  style.textContent = compileStyle({ source: descriptor.styles[0]!.content, filename: 'Workbench.vue', id: scopeId, scoped: true }).code
  document.head.append(style)
  try {
    const workspace = wrapper.get('.db-workspace').element
    expect(getComputedStyle(workspace).height).toBe('calc(100vh - 112px)')
    expect(getComputedStyle(workspace).overflow).toBe('hidden')
    expect(getComputedStyle(wrapper.get('.db-workspace-shell').element).minHeight).toBe('0')
    expect(getComputedStyle(wrapper.get('.db-workspace-tree').element).overflow).toBe('auto')
    expect(getComputedStyle(wrapper.get('.db-workspace-result').element).overflow).toBe('auto')
    expect(getComputedStyle(wrapper.get('.db-workspace-output').element).minHeight).toBe('0')
    const resizer = wrapper.get<HTMLElement>('.db-workspace-editor-resizer')
    expect(getComputedStyle(resizer.element).height).toBe('260px')
    expect(getComputedStyle(resizer.element).minHeight).toBe('120px')
    expect(getComputedStyle(resizer.element).maxHeight).toContain('520px')
    expect(getComputedStyle(resizer.element).resize).toBe('vertical')
    expect(resizer.get('textarea').attributes('height')).toBe('100%')
    resizer.element.style.height = '360px'
    expect(getComputedStyle(resizer.element).height).toBe('360px')
    expect(getComputedStyle(workspace).height).toBe('calc(100vh - 112px)')
    await workbenchTab(wrapper, '执行信息').trigger('click')
    await flushPromises()
    expect(getComputedStyle(wrapper.get('.db-workspace-execution').element).overflow).toBe('auto')
  } finally {
    style.remove()
  }
})

it('工作台异步按钮执行查询从提交到排队运行只显示自身spinner，结束恢复图标', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const requests = mockWorkbenchData()
  const { wrapper } = await mountConnectedWorkbench()
  const submit = api.submitTask.getMockImplementation()!
  const getTask = api.getTask.getMockImplementation()!
  let resumeSubmission!: () => void
  let status: TaskView['status'] = 'queued'
  api.submitTask.mockImplementation(async (...args) => {
    const accepted = await submit(...args)
    await new Promise<void>(resolve => { resumeSubmission = resolve })
    return accepted
  })
  api.getTask.mockImplementation(async id => ({ ...await getTask(id), status }))
  const execute = workbenchCommand(wrapper, '执行查询')
  expect(execute.findAll('.anticon-play-circle')).toHaveLength(1)
  await execute.trigger('click')
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual(['执行查询'])
  expect(execute.find('.anticon-play-circle').exists()).toBe(false)
  expect(execute.findAll('.anticon-loading')).toHaveLength(1)
  resumeSubmission()
  await flushPromises()
  expect(wrapper.text()).toContain('排队中')
  expect(loadingCommands(wrapper)).toEqual(['执行查询'])
  status = 'running'
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  expect(wrapper.text()).toContain('执行中')
  expect(loadingCommands(wrapper)).toEqual(['执行查询'])
  await execute.trigger('click')
  expect([...requests.values()].filter(request => request.kind === 'sql')).toHaveLength(1)
  status = 'succeeded'
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual([])
  expect(execute.findAll('.anticon-play-circle')).toHaveLength(1)
})

it('工作台异步按钮自动读取可见库时全部不转，仍禁止重复刷新', async () => {
  mockWorkbenchData()
  const getTask = api.getTask.getMockImplementation()!
  api.getTask.mockImplementation(async id => ({ ...await getTask(id), status: 'running' }))
  const { wrapper } = await mountConnectedWorkbench()
  expect(loadingCommands(wrapper)).toEqual([])
  expect(wrapper.get('button[aria-label="刷新可见库"]').attributes('disabled')).toBeDefined()
  expect(workbenchCommand(wrapper, '刷新结构').attributes('disabled')).toBeDefined()
  expect(wrapper.get('.db-workspace-browser-footer').text()).toContain('正在读取可见数据库')
})

it.each(['mysql', 'postgresql'])('工作台异步按钮%s刷新结构独占自身链路loading，结束恢复图标', async engine => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const requests = mockWorkbenchData()
  api.getInstance.mockResolvedValue({ ...instance, engine })
  const { wrapper } = await mountConnectedWorkbench()
  const getTask = api.getTask.getMockImplementation()!
  let status: TaskView['status'] = 'queued'
  let tableFinished = false
  api.getTask.mockImplementation(async id => {
    const request = requests.get(id)!
    return { ...await getTask(id), status: engine === 'postgresql' && request.kind === 'metadata' && request.operation === 'tables'
      ? tableFinished ? 'succeeded' : 'running' : status }
  })
  const refresh = workbenchCommand(wrapper, '刷新结构')
  const before = requests.size
  await refresh.trigger('click')
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual(['刷新结构'])
  expect(refresh.find('.anticon-reload').exists()).toBe(false)
  expect(refresh.findAll('.anticon-loading')).toHaveLength(1)
  await refresh.trigger('click')
  expect(requests.size).toBe(before + 1)
  status = 'running'
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual(['刷新结构'])
  status = 'succeeded'
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  if (engine === 'postgresql') {
    expect(wrapper.get('.db-workspace-browser-footer').text()).toContain('正在读取表列表')
    expect(loadingCommands(wrapper)).toEqual(['刷新结构'])
    tableFinished = true
    await vi.advanceTimersByTimeAsync(1500)
    await flushPromises()
  }
  expect(loadingCommands(wrapper)).toEqual([])
  expect(refresh.findAll('.anticon-reload')).toHaveLength(1)
})

it('工作台异步按钮取消结构读取接管spinner，停止核对期间保留直到终态', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const requests = mockWorkbenchData()
  const { wrapper } = await mountConnectedWorkbench()
  const getTask = api.getTask.getMockImplementation()!
  let status: TaskView['status'] = 'running'
  let finishCancel!: (value: TaskView) => void
  api.getTask.mockImplementation(async id => ({ ...await getTask(id), status }))
  api.cancelTask.mockImplementation(() => new Promise(resolve => { finishCancel = resolve }))
  await workbenchCommand(wrapper, '刷新结构').trigger('click')
  await flushPromises()
  await workbenchCommand(wrapper, '取消结构读取').trigger('click')
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual(['取消结构读取'])
  const ids = [...requests.keys()], id = ids[ids.length - 1]!
  finishCancel({ ...await getTask(id), status: 'stopping' } as TaskView)
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual(['取消结构读取'])
  expect(workbenchCommand(wrapper, '取消结构读取').attributes('disabled')).toBeDefined()
  status = 'recovering'
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual(['取消结构读取'])
  status = 'cancelled'
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual([])
  expect(workbenchCommand(wrapper, '取消结构读取')).toBeUndefined()
})

it.each(['执行查询', '刷新结构'])('工作台异步按钮%s取消请求失败后仍核对任务，切换实例清除旧来源', async command => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  mockWorkbenchData()
  const { wrapper, page } = await mountConnectedWorkbench()
  const getTask = api.getTask.getMockImplementation()!
  api.getTask.mockImplementation(async id => ({ ...await getTask(id), status: 'running' }))
  api.cancelTask.mockRejectedValue(new Error('network unavailable'))
  await workbenchCommand(wrapper, command).trigger('click')
  await flushPromises()
  const cancel = command === '执行查询' ? '取消查询' : '取消结构读取'
  await workbenchCommand(wrapper, cancel).trigger('click')
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual([cancel.replace(/\s/g, '')])
  expect(workbenchCommand(wrapper, command).attributes('disabled')).toBeDefined()
  await workbenchTab(wrapper, '执行信息').trigger('click')
  await flushPromises()
  const target = wrapper.findAllComponents(TaskStatus).find(component => component.props('task')?.kind === (command === '执行查询' ? 'sql' : 'metadata'))!
  expect(target.text()).toContain('取消请求结果未知，继续核对任务状态')
  expect(target.text()).toContain('执行中')
  expect(target.text()).not.toContain('已完成')
  api.getInstance.mockResolvedValue({ ...instance, id: 2, name: '新实例' })
  page.instanceId = 2
  await page.chooseInstance()
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual([])
  expect(wrapper.get('button[aria-label="刷新可见库"]').attributes('disabled')).toBeDefined()
})

it.each([
  { command: '扫描 Key', type: 'scan' },
  { command: '继续扫描下一页', type: 'scan' },
  { command: '下一页成员', type: 'list_page' },
])('工作台异步按钮Redis $command只点亮自身，排队运行至终态', async ({ command, type }) => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  mockWorkbenchData()
  api.getInstance.mockResolvedValue({ ...instance, engine: 'redis', connection_options: { database: 0 } })
  api.getResult.mockResolvedValue({ ...result, columns: [{ ordinal: 0, name: 'next_cursor', type: 'string' }], rows: [['next-cursor']] })
  const { wrapper, page } = await mountConnectedWorkbench()
  Object.assign(page.redis, { type, key: 'demo' })
  await page.execute()
  await flushPromises()
  const getTask = api.getTask.getMockImplementation()!
  let status: TaskView['status'] = 'queued'
  api.getTask.mockImplementation(async id => ({ ...await getTask(id), status }))
  await workbenchCommand(wrapper, command).trigger('click')
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual([command.replace(/\s/g, '')])
  expect(workbenchCommand(wrapper, command).findAll('.anticon-loading')).toHaveLength(1)
  status = 'running'
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual([command.replace(/\s/g, '')])
  status = 'succeeded'
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual([])
})

it('刷新可见库只在读取库列表时转动，自动读取表列表期间禁用且不重复提交', async () => {
  const requests = mockWorkbenchData()
  const { wrapper } = await mountConnectedWorkbench()
  const getResult = api.getResult.getMockImplementation()!
  let finishDatabases!: (value: typeof result) => void
  let finishTables!: (value: typeof result) => void
  let databaseResult!: typeof result
  let tableResult!: typeof result
  api.getResult.mockImplementation(async (...args) => {
    const request = requests.get(args[0])!
    const data = await getResult(...args)
    if (request.kind === 'metadata' && request.operation === 'databases') {
      databaseResult = data
      return new Promise(resolve => { finishDatabases = resolve })
    }
    if (request.kind === 'metadata' && request.operation === 'tables') {
      tableResult = data
      return new Promise(resolve => { finishTables = resolve })
    }
    return data
  })
  const refresh = wrapper.get('button[aria-label="刷新可见库"]')
  expect(refresh.findAll('.anticon-reload')).toHaveLength(1)
  expect(refresh.find('.anticon-loading').exists()).toBe(false)
  const initialCount = requests.size
  await refresh.trigger('click')
  await flushPromises()
  expect(refresh.classes()).toContain('ant-btn-loading')
  expect(refresh.find('.anticon-reload').exists()).toBe(false)
  expect(refresh.findAll('.anticon-loading')).toHaveLength(1)
  expect(wrapper.get('.db-workspace-browser-footer').text()).toContain('正在读取可见数据库')
  expect(wrapper.find('.db-workspace-browser-footer .ant-spin').exists()).toBe(false)
  await refresh.trigger('click')
  expect(requests.size).toBe(initialCount + 1)
  finishDatabases(databaseResult)
  await flushPromises()
  expect(refresh.classes()).not.toContain('ant-btn-loading')
  expect(refresh.findAll('.anticon-reload')).toHaveLength(1)
  expect(refresh.find('.anticon-loading').exists()).toBe(false)
  expect(refresh.attributes('disabled')).toBeDefined()
  expect(wrapper.get('.db-workspace-browser-footer').text()).toContain('正在读取表列表')
  expect(wrapper.find('.db-workspace-browser-footer .ant-spin').exists()).toBe(false)
  await refresh.trigger('click')
  expect(requests.size).toBe(initialCount + 2)
  finishTables(tableResult)
  await flushPromises()
  expect(refresh.classes()).not.toContain('ant-btn-loading')
  expect(refresh.findAll('.anticon-reload')).toHaveLength(1)
  expect(refresh.find('.anticon-loading').exists()).toBe(false)
  expect(refresh.attributes('disabled')).toBeUndefined()
  expect(wrapper.get('.db-workspace-browser-footer').text()).toContain('已加载 2 个表')
})

it.each([
  { status: 'stopping', label: '取消处理中', confirmed: false },
  { status: 'recovering', label: '停止待确认', confirmed: false },
  { status: 'cancelled', label: '已取消', confirmed: true },
])('工作台$status保留执行状态，查询与对象任务均隐藏停止依据', async ({ status, label, confirmed }) => {
  const requests = mockWorkbenchData()
  const getTask = api.getTask.getMockImplementation()!
  api.getTask.mockImplementation(async id => ({
    ...await getTask(id),
    status: requests.get(id)?.kind === 'sql' ? status : 'succeeded',
    stop_confirmation: { confirmed, evidence_marker: 'workbench-stop-evidence' }
  }))
  const { wrapper, page } = await mountConnectedWorkbench()
  await page.execute()
  await flushPromises()
  await workbenchTab(wrapper, '执行信息').trigger('click')
  await flushPromises()
  const execution = wrapper.get('.db-workspace-execution')
  expect(execution.findAllComponents(TaskStatus)).toHaveLength(2)
  expect(execution.text()).toContain(label)
  expect(execution.find('details').exists()).toBe(false)
  expect(execution.text()).not.toContain('查看停止依据')
  expect(execution.text()).not.toContain('workbench-stop-evidence')
  expect(execution.findAll('.db-muted').map(item => item.text())).not.toContain('停止已确认')
  expect(execution.findAll('.db-muted').map(item => item.text())).not.toContain('停止待确认')
  if (status !== 'cancelled') expect(execution.text()).toContain('已申请停止，目标停止状态尚未确认；不会自动重新执行。')
})

it('工作台连接栏、对象浏览器、编辑器工具栏与三页签组成一个工作区，元数据状态只在执行信息', async () => {
  const requests = mockWorkbenchData()
  const { wrapper, page } = await mountConnectedWorkbench()
  expect(wrapper.get('.db-workspace-connection').text()).toContain('账号：ro')
  expect(wrapper.get('.db-workspace-connection').text()).toContain('60 秒')
  expect(wrapper.get('.db-workspace-browser').text()).toContain('orders')
  expect(wrapper.get('.db-workspace-browser').text()).not.toContain('任务 #')
  expect(wrapper.get('.db-workspace-editor-toolbar').text()).toContain('执行查询')
  expect(wrapper.get('.db-workspace-editor-toolbar').text()).toContain('刷新结构')
  expect(wrapper.findAll('[role="tab"]').map(tab => tab.text())).toEqual(['查询结果', '表结构', '执行信息'])
  expect([...requests.values()].filter(request => request.kind === 'metadata').map(request => request.operation)).toEqual(['databases', 'tables'])
  await wrapper.findAll('.ant-tree-title').find(node => node.text() === 'orders')!.trigger('click')
  await flushPromises()
  expect(wrapper.get('.db-workspace-browser').text()).toContain('order_id')
  expect(wrapper.get('.db-workspace-browser').text()).toContain('bigint')
  await workbenchTab(wrapper, '表结构').trigger('click')
  await flushPromises()
  expect(wrapper.get('.db-workspace-structure').text()).toContain('payload')
  expect(wrapper.get('.db-workspace-structure').text()).not.toContain('索引')
  await wrapper.findAll('button').find(button => button.text().includes('生成 SELECT'))!.trigger('click')
  expect(page.sql).toBe('SELECT * FROM `orders`;')
  expect([...requests.values()].every(request => request.kind === 'metadata')).toBe(true)
  await workbenchTab(wrapper, '执行信息').trigger('click')
  await flushPromises()
  expect(wrapper.get('.db-workspace-execution').text()).toContain('元数据：columns')
})

it('对象浏览器按真实对象名与当前字段检索，搜索不提交数据库任务', async () => {
  mockWorkbenchData()
  const { wrapper, page } = await mountConnectedWorkbench()
  page.chooseObject('orders')
  await flushPromises()
  api.submitTask.mockClear()
  await wrapper.get('input[placeholder="搜索表或字段"]').setValue('payload')
  expect(wrapper.get('.db-workspace-browser').text()).toContain('orders')
  expect(wrapper.get('.db-workspace-browser').text()).toContain('payload')
  expect(wrapper.get('.db-workspace-browser').text()).not.toContain('customers')
  expect(api.submitTask).not.toHaveBeenCalled()
})

it('PostgreSQL选择Schema后重新读取对象，生成SELECT使用当前Schema并转义标识符', async () => {
  const requests = mockWorkbenchData()
  api.getInstance.mockResolvedValue({ ...instance, engine: 'postgresql', connection_options: { default_database: 'sales', default_schema: 'public' } })
  const { wrapper, page } = await mountConnectedWorkbench()
  page.schema = 'audit'
  page.schemaChanged()
  await page.loadContext()
  await flushPromises()
  expect([...requests.values()].some(request => request.kind === 'metadata' && request.operation === 'tables' && request.context.schema === 'audit')).toBe(true)
  page.chooseObject('log"data')
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text().includes('生成 SELECT'))!.trigger('click')
  expect(page.sql).toBe('SELECT * FROM "audit"."log""data";')
})

it('PostgreSQL读完全部可见Schema保存页后选择实际可见项，不向不可见public读取表', async () => {
  const requests = mockWorkbenchData()
  api.getInstance.mockResolvedValue({ ...instance, engine: 'postgresql', connection_options: { default_database: 'sales', default_schema: 'public' } })
  const getResult = api.getResult.getMockImplementation()!
  api.getResult.mockImplementation(async (...args) => {
    const request = requests.get(args[0])!
    const data = await getResult(...args)
    return request.kind === 'metadata' && request.operation === 'schemas'
      ? { ...data, rows: args[1] === 1 ? [['audit']] : [['reporting']], page_size: 1, saved_total: 2 }
      : data
  })
  const { wrapper, page } = await mountConnectedWorkbench()
  expect(page.schema).toBe('audit')
  expect([...requests.values()].flatMap(request => request.kind === 'metadata' && request.operation === 'tables' ? [request.context.schema] : [])).toEqual(['audit'])
  expect(wrapper.get('.db-workspace-browser').text()).toContain('audit')
  expect(wrapper.get('.db-workspace-browser').text()).toContain('reporting')
  expect(wrapper.get('.db-workspace-browser').text()).not.toContain('public')
})

it('默认库与Schema未被元数据返回时不冒充可见树节点，仍可手动输入上下文查询', async () => {
  mockWorkbenchData()
  api.getInstance.mockResolvedValue({ ...instance, engine: 'postgresql', connection_options: { default_database: 'manual_db', default_schema: 'manual_schema' } })
  const { wrapper, page } = await mountConnectedWorkbench()
  expect(wrapper.get('.db-workspace-browser').text()).not.toContain('manual_db')
  expect(wrapper.get('.db-workspace-browser').text()).not.toContain('manual_schema')
  page.database = 'manual_db'
  page.databaseChanged()
  page.schema = 'manual_schema'
  page.schemaChanged()
  await page.execute()
  expect(api.submitTask).toHaveBeenCalledWith({ kind: 'sql', instance_id: 1,
    context: { database: 'manual_db', schema: 'manual_schema' }, sql: 'SELECT 1;' }, expect.any(AbortSignal))
  expect(wrapper.get('.db-workspace-browser').text()).not.toContain('manual_db')
  expect(wrapper.get('.db-workspace-browser').text()).not.toContain('manual_schema')
})

it('工作台切换库立即清空结果与旧对象，迟到的结果分页不能回显到新上下文', async () => {
  mockWorkbenchData()
  const { wrapper, page } = await mountConnectedWorkbench()
  await page.execute()
  await flushPromises()
  expect(wrapper.findComponent(ResultTable).text()).toContain('fresh-value')
  let finish!: (value: typeof result) => void
  api.getResult.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  wrapper.findComponent(ResultTable).vm.$emit('page', 2, 20)
  await flushPromises()
  page.database = 'archive'
  page.databaseChanged()
  expect((wrapper.vm as unknown as { session: { result: { value: unknown } } }).session.result.value).toBeNull()
  await nextTick()
  expect(wrapper.findComponent(ResultTable).exists()).toBe(false)
  await page.loadContext()
  await flushPromises()
  finish({ ...result, rows: [['1', 'OLD-RESULT']], page: 2 })
  await flushPromises()
  expect(wrapper.get('.db-workspace-browser').text()).toContain('ledger')
  expect(wrapper.get('.db-workspace-browser').text()).not.toContain('orders')
  expect(wrapper.text()).not.toContain('OLD-RESULT')
  expect(wrapper.findComponent(ResultTable).exists()).toBe(false)
})

it('工作台无查询权限不加载实例和策略，也不能绕过按钮直接提交任务', async () => {
  state.permissions = new Set(['database:read'])
  const wrapper = mount(Workbench, options)
  wrappers.push(wrapper)
  await flushPromises()
  await (wrapper.vm as unknown as { execute: () => Promise<void> }).execute()
  expect(wrapper.text()).toContain('暂无数据库查询权限')
  expect(api.listInstances).not.toHaveBeenCalled()
  expect(api.clientPolicy).not.toHaveBeenCalled()
  expect(api.submitTask).not.toHaveBeenCalled()
})

it('工作台切换实例时展示加载态并隐藏旧工作区，迟到的实例响应不能覆盖新选择', async () => {
  mockWorkbenchData()
  const { wrapper, page } = await mountConnectedWorkbench()
  let finish!: (value: typeof instance) => void
  api.getInstance.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  page.instanceId = 2
  const pending = page.chooseInstance()
  await flushPromises()
  expect(wrapper.find('.ant-skeleton').exists()).toBe(true)
  expect(wrapper.find('.db-workspace-browser').exists()).toBe(false)
  expect(wrapper.text()).not.toContain('orders')
  api.getInstance.mockResolvedValueOnce({ ...instance, id: 3, name: '新实例', connection_options: { default_database: 'archive' } })
  page.instanceId = 3
  await page.chooseInstance()
  await flushPromises()
  finish({ ...instance, id: 2, name: '旧实例' })
  await pending
  await flushPromises()
  expect(page.instanceId).toBe(3)
  expect(page.database).toBe('archive')
  expect(wrapper.get('.db-workspace-browser').text()).toContain('ledger')
  expect(wrapper.text()).not.toContain('旧实例')
})

it('工作台初始化策略未完成时禁用实例选择，完成深链恢复后才允许切换', async () => {
  let finish!: (value: { execution_timeout_seconds: number; max_result_rows: number; max_result_bytes: number; result_ttl_minutes: number }) => void
  api.clientPolicy.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  state.query = { instance_id: '1' }
  const wrapper = mount(Workbench, options)
  wrappers.push(wrapper)
  await flushPromises()
  expect(wrapper.get('.db-workspace-instance').findComponent(Select).props('disabled')).toBe(true)
  expect(api.getInstance).not.toHaveBeenCalled()
  finish({ execution_timeout_seconds: 60, max_result_rows: 1000, max_result_bytes: 5242880, result_ttl_minutes: 30 })
  await flushPromises()
  expect(api.getInstance).toHaveBeenCalledWith(1)
  expect(wrapper.get('.db-workspace-instance').findComponent(Select).props('disabled')).toBe(false)
})

it('工作台对象列表读取本次保存的所有分页，加载更多不重复执行元数据查询', async () => {
  const requests = mockWorkbenchData()
  const getResult = api.getResult.getMockImplementation()!
  api.getResult.mockImplementation(async (...args) => {
    const request = requests.get(args[0])!
    const data = await getResult(...args)
    return request.kind === 'metadata' && request.operation === 'tables'
      ? { ...data, rows: args[1] === 1 ? [['first_table']] : [['second_table']], saved_total: 2, page_size: 1 }
      : data
  })
  const { wrapper } = await mountConnectedWorkbench()
  expect(wrapper.get('.db-workspace-browser').text()).toContain('first_table')
  expect(wrapper.get('.db-workspace-browser').text()).toContain('second_table')
  expect([...requests.values()].filter(request => request.kind === 'metadata' && request.operation === 'tables')).toHaveLength(1)
})

it('工作台字段读取在途切换对象时立即读取新对象，迟到字段不能挂到新对象下', async () => {
  const requests = mockWorkbenchData()
  const { wrapper, page } = await mountConnectedWorkbench()
  let finish!: (value: unknown) => void
  api.getResult.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  page.chooseObject('orders')
  await flushPromises()
  page.chooseObject('customers')
  await flushPromises()
  expect([...requests.values()].some(request => request.kind === 'metadata' && request.operation === 'columns' && request.context.table === 'customers')).toBe(true)
  finish({ ...result, columns: [{ ordinal: 0, name: 'name', type: 'string' }], rows: [['LATE_FIELD']] })
  await flushPromises()
  await workbenchTab(wrapper, '表结构').trigger('click')
  await flushPromises()
  expect(wrapper.get('.db-workspace-structure').text()).toContain('customers')
  expect(wrapper.get('.db-workspace-structure').text()).toContain('payload')
  expect(wrapper.get('.db-workspace-browser').text()).not.toContain('LATE_FIELD')
  expect(wrapper.get('.db-workspace-structure').text()).not.toContain('LATE_FIELD')
})

it('工作台结构读取失败只在浏览器给出入口，完整任务与错误位于执行信息', async () => {
  mockWorkbenchData()
  api.getTask.mockImplementation(async id => ({ ...task, task_id: id, kind: 'metadata', status: 'failed', error_message: '读取结构被拒绝' }))
  const { wrapper } = await mountConnectedWorkbench()
  const browser = wrapper.get('.db-workspace-browser')
  expect(browser.text()).not.toContain('任务 #')
  expect(browser.text()).not.toContain('读取结构被拒绝')
  await browser.findAll('a').find(link => link.text().includes('查看执行信息'))!.trigger('click')
  await flushPromises()
  expect(wrapper.get('.db-workspace-execution').text()).toContain('读取结构被拒绝')
  expect(api.getResult).not.toHaveBeenCalled()
})

it('工作台异步按钮取消查询接管运行任务spinner，停止确认期间禁止再次取消与执行', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const requests = mockWorkbenchData()
  const { wrapper, page } = await mountConnectedWorkbench()
  const getTask = api.getTask.getMockImplementation()!
  let cancelling = false
  api.getTask.mockImplementation(async id => ({ ...await getTask(id), status: cancelling ? 'cancelled' : 'running' }))
  api.cancelTask.mockImplementation(async id => {
    cancelling = true
    return { ...task, task_id: id, status: 'stopping', stop_confirmation: { confirmed: false } }
  })
  await page.execute()
  await flushPromises()
  const toolbar = wrapper.get('.db-workspace-editor-toolbar')
  expect(wrapper.text()).toContain('执行中')
  expect(loadingCommands(wrapper)).toEqual(['执行查询'])
  await toolbar.findAll('button').find(button => button.text().includes('取消查询'))!.trigger('click')
  await flushPromises()
  expect(loadingCommands(wrapper)).toEqual(['取消查询'])
  expect(workbenchCommand(wrapper, '取消查询').find('.anticon-stop').exists()).toBe(false)
  expect(workbenchCommand(wrapper, '取消查询').findAll('.anticon-loading')).toHaveLength(1)
  expect(toolbar.findAll('button').find(button => button.text().includes('取消查询'))!.attributes('disabled')).toBeDefined()
  expect(toolbar.findAll('button').find(button => button.text().includes('执行查询'))!.attributes('disabled')).toBeDefined()
  expect(wrapper.text()).toContain('取消处理中')
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  expect(wrapper.text()).toContain('已取消')
  expect(loadingCommands(wrapper)).toEqual([])
  expect(workbenchCommand(wrapper, '取消查询').findAll('.anticon-stop')).toHaveLength(1)
  expect(api.cancelTask).toHaveBeenCalledTimes(1)
  expect([...requests.values()].filter(request => request.kind === 'sql')).toHaveLength(1)
})

it('工作台失败查询在结果页说明原因，执行信息保留完整任务而不伪造结果', async () => {
  mockWorkbenchData()
  const { wrapper, page } = await mountConnectedWorkbench()
  api.getTask.mockImplementation(async id => ({ ...task, task_id: id, status: 'failed', error_message: '仅允许 SELECT' }))
  api.getResult.mockClear()
  await page.execute()
  await flushPromises()
  expect(wrapper.get('.db-workspace-result').text()).toContain('仅允许 SELECT')
  expect(wrapper.findComponent(ResultTable).exists()).toBe(false)
  expect(api.getResult).not.toHaveBeenCalled()
  await workbenchTab(wrapper, '执行信息').trigger('click')
  await flushPromises()
  expect(wrapper.get('.db-workspace-execution').text()).toContain('失败')
})

it.each([
  { type: 'find', operation: { type: 'find', filter: { active: true }, projection: { name: 1 }, sort: [{ field: 'created', direction: -1 }], limit: 25 } },
  { type: 'count_documents', operation: { type: 'count_documents', filter: { active: true } } },
  { type: 'distinct', operation: { type: 'distinct', filter: { active: true }, field: 'name', limit: 25 } }
])('MongoDB工作区保留$type受控表单与完整请求', async ({ type, operation }) => {
  mockWorkbenchData()
  api.getInstance.mockResolvedValue({ ...instance, engine: 'mongodb', connection_options: { default_database: 'sales' } })
  const { wrapper, page } = await mountConnectedWorkbench()
  page.chooseObject('events')
  await flushPromises()
  Object.assign(page.mongo, { type, filter: '{"active":true}', projection: '{"name":1}', sort: '{"created":-1}', field: 'name', limit: 25 })
  api.submitTask.mockClear()
  await page.execute()
  await flushPromises()
  expect(wrapper.get('.db-workspace-editor').text()).toContain('只读操作')
  expect(api.submitTask).toHaveBeenCalledWith({ kind: 'mongo_read', instance_id: 1, context: { database: 'sales', collection: 'events' }, operation }, expect.any(AbortSignal))
  expect(wrapper.findAll('[role="tab"]').map(tab => tab.text())).toEqual(['查询结果', '表结构', '执行信息'])
})

it('工作台结果页保留分页与到期清理，执行信息可单独查看实际SQL和失败原因', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const requests = mockWorkbenchData()
  const { wrapper, page } = await mountConnectedWorkbench()
  const getResult = api.getResult.getMockImplementation()!
  api.getResult.mockImplementation(async (...args) => ({ ...await getResult(...args),
    expires_at: new Date(Date.now() + 1000).toISOString(), saved_total: 100, page: args[1] || 1, page_size: args[2] || 50 }))
  await page.execute()
  await flushPromises()
  const queryId = [...requests].find(([, request]) => request.kind === 'sql')![0]
  wrapper.findComponent(ResultTable).vm.$emit('page', 2, 20)
  await flushPromises()
  expect(api.getResult).toHaveBeenLastCalledWith(queryId, 2, 20, expect.any(AbortSignal))
  await vi.advanceTimersByTimeAsync(1000)
  await flushPromises()
  expect(wrapper.findComponent(ResultTable).exists()).toBe(false)
  expect(wrapper.text()).toContain('结果已过期')
  await workbenchTab(wrapper, '执行信息').trigger('click')
  await flushPromises()
  expect(wrapper.get('.db-workspace-execution').text()).toContain('实际执行')
})
it('集群下拉使用scope完整候选，选择后精确过滤，清空后恢复普通列表', async () => {
  api.listInstances.mockResolvedValue({ items: [{ ...instance, cluster_name: '共享集群' }], total: 1, cluster_names: ['共享集群', '另一个集群'] })
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  const selects = wrapper.findAllComponents(Select)
  expect(selects.some(item => item.props('placeholder') === '检测状态')).toBe(false)
  const cluster = selects.find(item => item.props('placeholder') === '集群名称')!
  expect(cluster).toBeTruthy()
  expect(cluster.props('showSearch')).toBe(true)
  expect(cluster.props('options')).toEqual([{ value: '共享集群', label: '共享集群' }, { value: '另一个集群', label: '另一个集群' }])
  cluster.vm.$emit('update:value', '共享集群')
  cluster.vm.$emit('change', '共享集群')
  await flushPromises()
  expect(api.listInstances).toHaveBeenLastCalledWith(expect.objectContaining({ cluster_name: '共享集群', page: 1 }))
  cluster.vm.$emit('update:value', undefined)
  cluster.vm.$emit('change', undefined)
  await flushPromises()
  expect(api.listInstances).toHaveBeenLastCalledWith(expect.objectContaining({ cluster_name: undefined }))
})
it('实例所有数据列提供服务端排序，切换排序复位页码且清除后恢复默认排序', async () => {
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  const table = wrapper.findComponent(Table)
  const columns = table.props('columns')!
  expect(columns.filter(column => column.key !== 'action').every(column => column.sorter === true)).toBe(true)
  await wrapper.get('thead .ant-table-column-sorters').trigger('click')
  await flushPromises()
  expect(api.listInstances).toHaveBeenLastCalledWith(expect.objectContaining({ sort_by: 'name', sort_order: 'asc' }))
  table.vm.$emit('change', { current: 3, pageSize: 20 }, {}, {})
  await flushPromises()
  for (const field of ['name', 'description', 'engine', 'business_line', 'cluster_name', 'cluster_role', 'version', 'address', 'apps', 'hosts', 'status', 'created_at']) {
    table.vm.$emit('change', { current: 3, pageSize: 20 }, {}, { field, columnKey: field, order: 'ascend' })
    await flushPromises()
    expect(api.listInstances).toHaveBeenLastCalledWith(expect.objectContaining({ sort_by: field, sort_order: 'asc', page: 1 }))
  }
  table.vm.$emit('change', { current: 1, pageSize: 20 }, {}, { field: 'name', order: 'descend' })
  await flushPromises()
  expect(api.listInstances).toHaveBeenLastCalledWith(expect.objectContaining({ sort_by: 'name', sort_order: 'desc' }))
  table.vm.$emit('change', { current: 1, pageSize: 20 }, {}, { field: 'name', order: undefined })
  await flushPromises()
  expect(api.listInstances).toHaveBeenLastCalledWith(expect.objectContaining({ sort_by: undefined, sort_order: undefined }))
})
it('实例名称随表格滚动，拖动列宽后保留新宽度和横向滚动范围', async () => {
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  const table = wrapper.findComponent(Table)
  const name = table.props('columns')!.find(column => column.key === 'name')!
  expect(name.fixed).toBeUndefined()
  expect(name.resizable).toBe(true)
  const originalScroll = table.props('scroll')!.x as number
  table.vm.$emit('resizeColumn', 260, name)
  await flushPromises()
  expect(table.props('columns')!.find(column => column.key === 'name')!.width).toBe(260)
  expect(table.props('scroll')!.x).toBe(originalScroll + 90)
})
it('实例选择框列固定左侧，名称继续滚动且操作列固定右侧', async () => {
  state.permissions.add('database:delete')
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  const table = wrapper.findComponent(Table)
  expect(table.props('rowSelection')).toMatchObject({ fixed: true, columnWidth: 48 })
  expect(table.props('columns')!.find(column => column.key === 'name')!.fixed).toBeUndefined()
  expect(table.props('columns')!.find(column => column.key === 'action')!.fixed).toBe('right')
  expect(wrapper.get('thead .ant-table-selection-column').classes()).toContain('ant-table-cell-fix-left')
})
it.each([
  [true, 'succeeded', '启用', '成功', 'ant-tag-success', 'ant-tag-success'],
  [true, 'failed', '启用', '失败', 'ant-tag-success', 'ant-tag-error'],
  [true, 'untested', '启用', '未检测', 'ant-tag-success', 'ant-tag-default'],
  [false, 'failed', '停用', '失败', 'ant-tag-default', 'ant-tag-error'],
  [false, 'succeeded', '停用', '成功', 'ant-tag-default', 'ant-tag-success'],
  [false, 'untested', '停用', '未检测', 'ant-tag-default', 'ant-tag-default'],
])('实例启用=%s、检测=%s分别展示两个标签，摘要仅保留检测悬浮提示', async (enabled, status, registration, detection, registrationColor, detectionColor) => {
  api.listInstances.mockResolvedValue({ items: [{ ...instance, enabled, last_probe_status: status, last_probe_message: '连接检测摘要' }], total: 1 })
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  const tags = wrapper.findAll('tbody .ant-tag')
  expect(tags.map(tag => tag.text())).toEqual([registration, detection])
  expect(tags[0]!.element.nextElementSibling?.textContent?.trim()).toBe('/')
  expect(tags[0]!.element.parentElement?.textContent?.replace(/\s/g, '')).toBe(`${registration}/${detection}`)
  expect(tags[0]!.classes()).toContain(registrationColor)
  expect(tags[1]!.classes()).toContain(detectionColor)
  expect(tags[0]!.attributes('title')).toBeUndefined()
  expect(tags[1]!.attributes('title')).toBe('连接检测摘要')
  expect(wrapper.find('tbody').text()).not.toContain('连接检测摘要')
})
it.each(['实例名称', '详情按钮'])('%s打开右侧详情且台账继续保留', async entry => {
  const wrapper = mount(InstanceList, { ...options, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  const trigger = entry === '实例名称'
    ? wrapper.get('tbody a')
    : wrapper.findAll('tbody button').find(button => button.text() === '详情')!
  await trigger.trigger('click')
  await flushPromises()
  expect(document.querySelector('.ant-drawer-title')?.textContent).toBe(instance.name)
  expect(document.querySelector('.ant-drawer-right')).toBeTruthy()
  expect(document.body.textContent).toContain('查询角色')
  expect(document.body.textContent).toContain('最近连接测试')
  const actions = [...document.querySelectorAll<HTMLButtonElement>('.ant-drawer button')].map(button => button.textContent?.replace(/\s/g, ''))
  expect(actions).not.toContain('连接测试')
  expect(actions).not.toContain('编辑实例')
  expect(actions).not.toContain('进入SQL工作台')
  expect(wrapper.find('tbody tr[data-row-key="1"]').exists()).toBe(true)
  expect(navigation.push).not.toHaveBeenCalled()
})
it.each([
  ['/database/instances/1', '.ant-drawer-title', instance.name],
  ['/database/instances/1/edit', '.ant-modal-title', '编辑数据库实例'],
  ['/database/instances/new', '.ant-modal-title', '新建数据库实例'],
])('旧地址%s在台账上承载浮层', async (path, selector, title) => {
  state.route.path = path
  const wrapper = mount(InstanceList, { ...options, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  expect(wrapper.get('.op-hero-title').text()).toBe('数据库实例')
  expect(document.querySelector(selector)?.textContent).toBe(title)
})
it('仅查询权限访问旧详情地址时只取query背景，关闭返回SQL工作台', async () => {
  state.permissions = new Set(['database:query'])
  state.route.path = '/database/instances/1'
  const wrapper = mount(InstanceList, { ...options, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  expect(api.listInstances.mock.calls.every(([params]) => params.scope === 'query')).toBe(true)
  const close = document.querySelector<HTMLButtonElement>('.ant-drawer-close')!
  expect(close).toBeTruthy()
  close.click()
  await flushPromises()
  expect(navigation.replace).toHaveBeenLastCalledWith({ path: '/database/workbench' })
})
it('编辑使用居中弹窗，取消不保存也不修改原连接配置', async () => {
  api.getInstance.mockResolvedValue({ ...instance, connection_options: { ...instance.connection_options } })
  const wrapper = mount(InstanceList, { ...options, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  await wrapper.findAll('tbody button').find(button => button.text() === '编辑')!.trigger('click')
  await flushPromises()
  expect(document.querySelector('.ant-modal-centered .ant-modal-title')?.textContent).toBe('编辑数据库实例')
  const editor = wrapper.findComponent(InstanceEditor)
  expect(editor.exists()).toBe(true)
  const vm = editor.vm as unknown as { form: { connection_options: { default_database?: string } } }
  vm.form.connection_options.default_database = '临时输入'
  const cancel = [...document.querySelectorAll<HTMLButtonElement>('.ant-modal button')].find(button => button.textContent?.replace(/\s/g, '') === '取消')!
  cancel.click()
  await flushPromises()
  expect(api.updateInstance).not.toHaveBeenCalled()
  expect(api.createInstance).not.toHaveBeenCalled()
  await expect(api.getInstance.mock.results[0]?.value).resolves.toMatchObject({ connection_options: { default_database: 'sales' } })
  expect(wrapper.findComponent(InstanceEditor).exists()).toBe(false)
})
it.each([true, false])('详情关联表格保留数据，CMDB读取与授权权限为%s时遵循原边界', async allowed => {
  state.route.path = '/database/instances/1'
  state.permissions = new Set(['database:read', 'database:write', ...(allowed ? ['cmdb:read', 'database:grant'] : [])])
  api.getInstance.mockResolvedValue({ ...instance,
    apps: [{ id: 7, name: '交易应用', ...(allowed ? { language: 'Java', deploy_type: 'docker' } : {}) }],
    hosts: [{ id: 21, hostname: '数据库主机', ip: '192.0.2.21', ...(allowed ? { environment: 'prod' } : {}) }],
  })
  api.getInstanceRoles.mockResolvedValue({ items: [{ id: 4, name: '数据库查询角色' }] })
  const wrapper = mount(InstanceList, { ...options, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  const drawer = document.querySelector('.ant-drawer')!
  const tables = [...drawer.querySelectorAll('.ant-table table')]
  expect(tables).toHaveLength(2)
  expect(tables[0]?.textContent).toContain('交易应用')
  expect(tables[1]?.textContent).toContain('数据库主机')
  expect(tables[1]?.textContent).toContain('192.0.2.21')
  expect([...tables[0]!.querySelectorAll('th')].map(th => th.textContent)).toEqual(['应用名', '语言', '部署方式'])
  expect([...tables[1]!.querySelectorAll('th')].map(th => th.textContent)).toEqual(['主机名', 'IP 地址', '环境'])
  expect(tables[0]?.textContent).toContain(allowed ? 'Java' : '—')
  expect(tables[0]?.textContent).toContain(allowed ? 'Docker' : '—')
  expect(tables[1]?.textContent).toContain(allowed ? 'prod' : '—')
  expect(drawer.querySelector('.relation-note')).toBeNull()
  expect(drawer.querySelector('a[href="/cmdb/apps?id=7&related=database"]') !== null).toBe(allowed)
  expect(drawer.querySelector('a[href="/cmdb/hosts?id=21&related=database"]') !== null).toBe(allowed)
  expect(drawer.querySelector('.op-desc-table')?.textContent).toContain('查询角色')
  if (allowed) {
    expect(api.getInstanceRoles).toHaveBeenCalledWith(1)
    expect(drawer.textContent).toContain('数据库查询角色')
  } else {
    const roleRow = [...drawer.querySelectorAll('.ant-descriptions-row')].find(row => row.querySelector('.ant-descriptions-item-label')?.textContent === '查询角色')
    expect(roleRow?.querySelector('.ant-descriptions-item-content')?.textContent).toBe('—')
    expect(api.getInstanceRoles).not.toHaveBeenCalled()
    expect(api.roleOptions).not.toHaveBeenCalled()
  }
})
it.each([
  [false, true, '关闭', 'ant-tag-default', ''],
  [true, true, '开启', 'ant-tag-success', '验证证书及主机名'],
  [true, false, '开启', 'ant-tag-success', '加密但不验证证书'],
])('详情TLS=%s以状态标签显示，保留验证方式', async (tls, verify, text, colorClass, explanation) => {
  state.route.path = '/database/instances/1'
  api.getInstance.mockResolvedValue({ ...instance, connection_options: { ...instance.connection_options, tls, tls_verify: verify } })
  const wrapper = mount(InstanceList, { ...options, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  const row = [...document.querySelectorAll('.ant-drawer .ant-descriptions-row')].find(item => item.querySelector('.ant-descriptions-item-label')?.textContent === 'TLS')!
  expect(row.querySelector('.ant-tag')?.textContent).toBe(text)
  expect(row.querySelector('.ant-tag')?.classList.contains(colorClass)).toBe(true)
  expect(row.textContent).toContain(explanation)
})

type EditorActions = {
  step: number
  selectedRoles: number[]
  roleReady: boolean
  form: { name: string; description?: string | null }
  loadRoles: () => Promise<void>
  save: () => Promise<void>
}
const queryRoles = [{ id: 4, name: '查询角色甲' }, { id: 5, name: '查询角色乙' }]

it('详情角色请求待完成与读取失败分别显示，不推定为空授权', async () => {
  state.permissions = new Set(['database:grant'])
  state.route.path = '/database/instances/1'
  let rejectRoles!: (reason: Error) => void
  api.getInstanceRoles.mockImplementationOnce(() => new Promise((_, reject) => { rejectRoles = reject }))
  const wrapper = mount(InstanceList, { ...options, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  const summary = () => document.querySelector('.ant-drawer .op-desc-table')?.textContent || ''
  expect(summary()).toContain('加载中')
  expect(summary()).not.toContain('角色加载失败')
  expect(summary()).not.toContain('尚未授权查询角色')
  rejectRoles(new Error('读取失败'))
  await flushPromises()
  expect(summary()).toContain('角色加载失败')
  expect(summary()).not.toContain('尚未授权查询角色')
})

it('grant-only可编辑已有实例授权，实例字段只读且不加载维护候选', async () => {
  state.permissions = new Set(['database:grant', 'cmdb:read'])
  state.route.path = '/database/instances/1/edit'
  api.getInstanceRoles.mockResolvedValue({ items: [queryRoles[0]] })
  api.roleOptions.mockResolvedValue({ items: queryRoles })
  const wrapper = mount(InstanceList, { ...options, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  const actions = wrapper.findAll('tbody button').map(button => button.text().replace(/\s/g, ''))
  expect(actions).toContain('编辑')
  expect(actions).not.toContain('测试')
  const editor = wrapper.findComponent(InstanceEditor)
  expect(editor.exists()).toBe(true)
  const vm = editor.vm as unknown as EditorActions
  expect(vm.step).toBe(1)
  expect(api.getInstanceRoles).toHaveBeenCalledWith(1)
  expect(api.roleOptions).toHaveBeenCalledTimes(1)
  expect(api.compatibleCredentials).not.toHaveBeenCalled()
  expect(cmdb.listApps).not.toHaveBeenCalled()
  expect(cmdb.listHosts).not.toHaveBeenCalled()
  const input = [...document.querySelectorAll<HTMLInputElement>('.ant-modal input')].find(item => item.value === instance.host)!
  expect(input.disabled).toBe(true)
  const text = document.querySelector('.ant-modal')?.textContent || ''
  expect([...document.querySelectorAll('.ant-modal-footer button')].map(button => button.textContent?.replace(/\s/g, ''))).toEqual(['取消', '上一步', '保存'])
  expect(text).not.toContain('保存实例')
  vm.selectedRoles = [5]
  await vm.save()
  expect(api.updateInstanceRoles).toHaveBeenCalledWith(1, [5])
  expect(api.updateInstance).not.toHaveBeenCalled()
  expect(api.createInstance).not.toHaveBeenCalled()
})

it('write-only编辑不读取角色API，保存实例不隐式提交授权', async () => {
  state.permissions = new Set(['database:write'])
  const wrapper = mount(InstanceEditor, { ...options, props: { instanceId: 1, initialStep: 1 } })
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as EditorActions
  expect(api.getInstanceRoles).not.toHaveBeenCalled()
  expect(api.roleOptions).not.toHaveBeenCalled()
  vm.form.name = '修改实例'
  await vm.save()
  expect(api.updateInstance).toHaveBeenCalledTimes(1)
  expect(api.updateInstanceRoles).not.toHaveBeenCalled()
})

it.each(['getInstanceRoles', 'roleOptions'] as const)('角色初始化%s失败禁存，可重试且不空数组覆盖', async failed => {
  state.permissions = new Set(['database:grant'])
  api.getInstanceRoles.mockResolvedValue({ items: [queryRoles[0]] })
  api.roleOptions.mockResolvedValue({ items: queryRoles })
  api[failed].mockRejectedValueOnce(new Error('角色读取失败'))
  const wrapper = mount(InstanceEditor, { ...options, props: { instanceId: 1 }, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as EditorActions
  expect(vm.roleReady).toBe(false)
  if (failed === 'roleOptions') {
    expect(vm.selectedRoles).toEqual([4])
    expect(document.querySelector('.ant-modal')?.textContent).toContain('查询角色甲')
  }
  const save = [...document.querySelectorAll<HTMLButtonElement>('.ant-modal-footer button')].find(button => button.textContent?.replace(/\s/g, '') === '保存')!
  expect(save.disabled).toBe(true)
  await vm.save()
  expect(api.updateInstanceRoles).not.toHaveBeenCalled()
  await vm.loadRoles()
  await flushPromises()
  expect(vm.roleReady).toBe(true)
  expect(vm.selectedRoles).toEqual([4])
  expect(save.disabled).toBe(false)
})

it('单次保存先更新实例再授权，授权失败保留弹窗选择，重试只提交授权', async () => {
  state.permissions = new Set(['database:write', 'database:grant'])
  api.roleOptions.mockResolvedValue({ items: queryRoles })
  const wrapper = mount(InstanceEditor, { ...options, props: { instanceId: 1, initialStep: 1 }, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as EditorActions
  vm.form.name = '更新实例'
  vm.selectedRoles = [5]
  api.updateInstanceRoles.mockRejectedValueOnce(new Error('授权保存失败'))
  const success = vi.spyOn(message, 'success')
  try {
    await vm.save()
    await flushPromises()
    expect(api.updateInstance).toHaveBeenCalledTimes(1)
    expect(api.updateInstanceRoles).toHaveBeenLastCalledWith(1, [5])
    expect(api.updateInstance.mock.invocationCallOrder[0]).toBeLessThan(api.updateInstanceRoles.mock.invocationCallOrder[0]!)
    expect(vm.selectedRoles).toEqual([5])
    expect(document.querySelector('.ant-modal')?.textContent).toContain('授权保存失败')
    expect(document.querySelector('.ant-modal')?.textContent).toContain('实例已保存')
    const name = [...document.querySelectorAll<HTMLInputElement>('.ant-modal input')].find(input => input.value === '更新实例')!
    expect(name.disabled).toBe(true)
    expect(success).not.toHaveBeenCalled()
    expect(wrapper.emitted('saved')).toBeUndefined()
    await vm.save()
    expect(success).toHaveBeenCalledWith('保存成功')
    expect(api.updateInstance).toHaveBeenCalledTimes(1)
    expect(api.updateInstanceRoles).toHaveBeenCalledTimes(2)
    expect(api.createInstance).not.toHaveBeenCalled()
    expect(wrapper.emitted('saved')).toEqual([[1]])
  } finally {
    success.mockRestore()
  }
})

it('统一保存进行中阻止重复提交，并在实例成功后才请求授权', async () => {
  state.permissions = new Set(['database:write', 'database:grant'])
  const wrapper = mount(InstanceEditor, { ...options, props: { instanceId: 1, initialStep: 1 } })
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as EditorActions
  vm.form.name = '更新实例'
  vm.selectedRoles = [5]
  let finishInstance!: (value: typeof instance) => void
  api.updateInstance.mockImplementationOnce(() => new Promise(resolve => { finishInstance = resolve }))
  const save = vm.save()
  await flushPromises()
  await vm.save()
  expect(api.updateInstance).toHaveBeenCalledTimes(1)
  expect(api.updateInstanceRoles).not.toHaveBeenCalled()
  let finishGrant!: () => void
  api.updateInstanceRoles.mockImplementationOnce(() => new Promise<void>(resolve => { finishGrant = resolve }))
  finishInstance(instance)
  await flushPromises()
  await vm.save()
  expect(api.updateInstance).toHaveBeenCalledTimes(1)
  expect(api.updateInstanceRoles).toHaveBeenCalledTimes(1)
  expect(wrapper.emitted('saved')).toBeUndefined()
  finishGrant()
  await save
  expect(wrapper.emitted('saved')).toEqual([[1]])
})

it('切换实例后忽略旧角色初始化响应', async () => {
  state.permissions = new Set(['database:grant'])
  let finishOld!: (value: { items: typeof queryRoles }) => void
  api.getInstanceRoles.mockImplementation(async (id: number) => id === 1
    ? new Promise(resolve => { finishOld = resolve })
    : { items: [queryRoles[1]] })
  api.roleOptions.mockResolvedValue({ items: queryRoles })
  const wrapper = mount(InstanceEditor, { ...options, props: { instanceId: 1 } })
  wrappers.push(wrapper)
  await flushPromises()
  await wrapper.setProps({ instanceId: 2 })
  await flushPromises()
  const vm = wrapper.vm as unknown as EditorActions
  expect(vm.selectedRoles).toEqual([5])
  finishOld({ items: [queryRoles[0]] })
  await flushPromises()
  expect(vm.selectedRoles).toEqual([5])
  vm.selectedRoles = [4]
  await vm.save()
  expect(api.updateInstanceRoles).toHaveBeenCalledWith(2, [4])
})

it.each(['关闭', '切换'])('%s编辑对象后迟到授权结果不回写、不提示新对象成功', async action => {
  state.permissions = new Set(['database:grant'])
  api.roleOptions.mockResolvedValue({ items: queryRoles })
  const wrapper = mount(InstanceEditor, { ...options, props: { instanceId: 1 } })
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as EditorActions
  vm.selectedRoles = [4]
  let finishGrant!: () => void
  api.updateInstanceRoles.mockImplementationOnce(() => new Promise<void>(resolve => { finishGrant = resolve }))
  const success = vi.spyOn(message, 'success')
  try {
    const pending = vm.save()
    await flushPromises()
    if (action === '关闭') wrapper.unmount()
    else {
      await wrapper.setProps({ instanceId: 2 })
      await flushPromises()
      vm.selectedRoles = [5]
    }
    finishGrant()
    await pending
    expect(success).not.toHaveBeenCalled()
    if (action === '切换') expect(vm.selectedRoles).toEqual([5])
    expect(api.updateInstance).not.toHaveBeenCalled()
    expect(wrapper.emitted('saved')).toBeUndefined()
  } finally {
    success.mockRestore()
  }
})

it('新建加载授权候选，创建成功后授权失败保留新ID，重试不会重复创建', async () => {
  state.permissions = new Set(['database:write', 'database:grant'])
  const wrapper = mount(InstanceEditor, { ...options, props: { initialStep: 1 } })
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as EditorActions
  expect(api.getInstanceRoles).not.toHaveBeenCalled()
  expect(api.roleOptions).toHaveBeenCalledTimes(1)
  expect(wrapper.text()).not.toContain('保存授权')
  Object.assign(vm.form, { name: '新增实例', host: instance.host, credential_id: 1 })
  vm.selectedRoles = [4]
  api.createInstance.mockResolvedValue({ ...instance, id: 71 })
  api.updateInstanceRoles.mockRejectedValueOnce(new Error('授权保存失败'))
  await vm.save()
  expect(api.createInstance).toHaveBeenCalledTimes(1)
  expect(api.updateInstanceRoles).toHaveBeenLastCalledWith(71, [4])
  expect(wrapper.emitted('saved')).toBeUndefined()
  await vm.save()
  expect(api.createInstance).toHaveBeenCalledTimes(1)
  expect(api.updateInstance).not.toHaveBeenCalled()
  expect(api.updateInstanceRoles).toHaveBeenCalledTimes(2)
  expect(wrapper.emitted('saved')).toEqual([[71]])
})
it.each([
  ['传输中断', new AxiosError('Network Error', 'ERR_NETWORK', undefined, {})],
  ['执行超时', new AxiosError('timeout of 30000ms exceeded', 'ECONNABORTED', undefined, {})],
])('新建%s结果未知时锁定再次创建，刷新台账并提示按名称核对', async (_kind, failure) => {
  state.route.path = '/database/instances/new'
  state.permissions = new Set(['database:write', 'database:grant'])
  const wrapper = mount(InstanceList, { ...options, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  const editor = wrapper.findComponent(InstanceEditor)
  const vm = editor.vm as unknown as EditorActions
  Object.assign(vm.form, { name: '待核对实例', host: instance.host, credential_id: 1 })
  vm.step = 1
  vm.selectedRoles = [4]
  api.createInstance.mockRejectedValueOnce(failure)
  api.listInstances.mockClear()
  await vm.save()
  await flushPromises()
  expect(api.createInstance).toHaveBeenCalledTimes(1)
  expect(api.listInstances).toHaveBeenCalled()
  expect(api.updateInstanceRoles).not.toHaveBeenCalled()
  const modal = document.querySelector('.ant-modal')!
  expect(modal.textContent).toContain('创建结果未知')
  expect(modal.textContent).toContain('待核对实例')
  expect(modal.textContent).toContain('台账')
  expect(modal.textContent).toContain('继续配置查询授权')
  const save = [...modal.querySelectorAll<HTMLButtonElement>('.ant-modal-footer button')].find(button => button.textContent?.replace(/\s/g, '') === '保存')!
  expect(save.disabled).toBe(true)
  const name = [...modal.querySelectorAll<HTMLInputElement>('input')].find(input => input.value === '待核对实例')!
  expect(name.disabled).toBe(true)
  await vm.save()
  expect(api.createInstance).toHaveBeenCalledTimes(1)
  expect(editor.emitted('saved')).toBeUndefined()
  const cancel = [...modal.querySelectorAll<HTMLButtonElement>('.ant-modal-footer button')].find(button => button.textContent?.replace(/\s/g, '') === '取消')!
  expect(cancel.disabled).toBe(false)
  cancel.click()
  await flushPromises()
  expect(wrapper.findComponent(InstanceEditor).exists()).toBe(false)
})
it.each([
  ['校验失败', new ApiError(42201, '连接字段校验失败')],
  ['名称冲突', new ApiError(40901, '实例名称冲突')],
  ['HTTP明确失败', new AxiosError('Request failed with status code 409', 'ERR_BAD_REQUEST', undefined, {}, {
    status: 409, statusText: 'Conflict', headers: {}, data: { message: '实例名称冲突' }, config: { headers: new AxiosHeaders() }
  })],
])('新建%s允许修改表单后重试，不把明确失败当作结果未知', async (_kind, failure) => {
  const wrapper = mount(InstanceEditor, { ...options, props: { initialStep: 1 }, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as EditorActions
  Object.assign(vm.form, { name: '首次实例', host: instance.host, credential_id: 1 })
  api.createInstance.mockRejectedValueOnce(failure)
  await vm.save()
  await flushPromises()
  const name = [...document.querySelectorAll<HTMLInputElement>('.ant-modal input')].find(input => input.value === '首次实例')!
  expect(name.disabled).toBe(false)
  expect(document.querySelector('.ant-modal')?.textContent).not.toContain('创建结果未知')
  vm.form.name = '调整后实例'
  await vm.save()
  expect(api.createInstance).toHaveBeenCalledTimes(2)
  expect(api.createInstance).toHaveBeenLastCalledWith(expect.objectContaining({ name: '调整后实例' }))
  expect(wrapper.emitted('saved')).toEqual([[1]])
})
it('授权重试发现其他管理员变更时保留选择并阻止覆盖，重新加载后才允许确认', async () => {
  state.permissions = new Set(['database:write', 'database:grant'])
  api.roleOptions.mockResolvedValue({ items: queryRoles })
  api.getInstanceRoles.mockResolvedValue({ items: [queryRoles[0]] })
  const wrapper = mount(InstanceEditor, { ...options, props: { instanceId: 1, initialStep: 1 }, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as EditorActions
  vm.form.name = '更新实例'
  vm.selectedRoles = [5]
  api.updateInstanceRoles.mockRejectedValueOnce(new Error('授权保存失败'))
  await vm.save()
  api.getInstanceRoles.mockResolvedValue({ items: [] })
  await vm.save()
  expect(api.updateInstance).toHaveBeenCalledTimes(1)
  expect(api.updateInstanceRoles).toHaveBeenCalledTimes(1)
  expect(vm.selectedRoles).toEqual([5])
  expect(document.querySelector('.ant-modal')?.textContent).toContain('其他管理员')
  expect(vm.roleReady).toBe(false)
  await vm.loadRoles()
  expect(vm.selectedRoles).toEqual([])
  vm.selectedRoles = [5]
  await vm.save()
  expect(api.updateInstanceRoles).toHaveBeenCalledTimes(2)
  expect(wrapper.emitted('saved')).toEqual([[1]])
})
it('授权请求结果不明但服务器已生效时，重试重读后直接完成而不重复替换角色', async () => {
  state.permissions = new Set(['database:grant'])
  api.roleOptions.mockResolvedValue({ items: queryRoles })
  const wrapper = mount(InstanceEditor, { ...options, props: { instanceId: 1 } })
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as EditorActions
  vm.selectedRoles = [5]
  api.updateInstanceRoles.mockRejectedValueOnce(new Error('网络中断'))
  await vm.save()
  api.getInstanceRoles.mockResolvedValue({ items: [queryRoles[1]] })
  await vm.save()
  expect(api.updateInstanceRoles).toHaveBeenCalledTimes(1)
  expect(wrapper.emitted('saved')).toEqual([[1]])
})
it('实例保存失败时不提交授权且保留原始输入', async () => {
  state.permissions = new Set(['database:write', 'database:grant'])
  const wrapper = mount(InstanceEditor, { ...options, props: { instanceId: 1, initialStep: 1 } })
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as EditorActions
  vm.form.name = '待保存实例'
  vm.selectedRoles = [4]
  api.updateInstance.mockRejectedValueOnce(new Error('实例保存失败'))
  await vm.save()
  expect(api.updateInstanceRoles).not.toHaveBeenCalled()
  expect(vm.form.name).toBe('待保存实例')
  expect(vm.selectedRoles).toEqual([4])
  await vm.save()
  expect(api.updateInstance).toHaveBeenCalledTimes(2)
  expect(api.updateInstanceRoles).toHaveBeenCalledTimes(1)
})
it('仅改角色跳过实例更新，比较使用规范化字段且不包含无权限维护的关系', async () => {
  state.permissions = new Set(['database:write', 'database:grant'])
  api.getInstance.mockResolvedValue({ ...instance, description: ' 原有说明 ', app_ids: [7], host_ids: [21] })
  const wrapper = mount(InstanceEditor, { ...options, props: { instanceId: 1, initialStep: 1 } })
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as EditorActions
  vm.form.description = '原有说明'
  vm.selectedRoles = [5]
  await vm.save()
  expect(api.updateInstance).not.toHaveBeenCalled()
  expect(api.createInstance).not.toHaveBeenCalled()
  expect(api.updateInstanceRoles).toHaveBeenCalledWith(1, [5])
  expect(wrapper.emitted('saved')).toEqual([[1]])
})
it('仅改实例不替换原有角色授权，无改动保存也不发送写请求', async () => {
  state.permissions = new Set(['database:write', 'database:grant'])
  api.getInstanceRoles.mockResolvedValue({ items: [queryRoles[0]] })
  const wrapper = mount(InstanceEditor, { ...options, props: { instanceId: 1, initialStep: 1 } })
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as EditorActions
  vm.form.name = '更新实例'
  await vm.save()
  expect(api.updateInstance).toHaveBeenCalledTimes(1)
  expect(api.updateInstanceRoles).not.toHaveBeenCalled()
  expect(api.getInstanceRoles).toHaveBeenCalledTimes(1)
  const unchanged = mount(InstanceEditor, { ...options, props: { instanceId: 1, initialStep: 1 } })
  wrappers.push(unchanged)
  await flushPromises()
  await (unchanged.vm as unknown as EditorActions).save()
  expect(api.updateInstance).toHaveBeenCalledTimes(1)
  expect(api.updateInstanceRoles).not.toHaveBeenCalled()
  expect(unchanged.emitted('saved')).toEqual([[1]])
})
it.each([
  ['mysql.example.test', 3306, 'mysql.example.test:3306'],
  ['192.0.2.10', 5432, '192.0.2.10:5432'],
  ['2001:db8::10', 6379, '[2001:db8::10]:6379'],
])('连接地址直接使用列表数据%s:%s并正确处理IPv6', async (host, port, expected) => {
  api.listInstances.mockResolvedValue({ items: [{ ...instance, host, port }], total: 1 })
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  const headers = wrapper.findAll('thead th').map(item => item.text())
  expect(headers[headers.indexOf('版本') + 1]).toBe('连接地址')
  const address = wrapper.findAll('tbody tr[data-row-key="1"] td')[headers.indexOf('连接地址')]
  expect(address?.text()).toBe(expected)
  expect(api.getInstance).not.toHaveBeenCalled()
})
it('保存失败保留弹窗与输入，迟到保存不关闭新的编辑对象', async () => {
  state.route.path = '/database/instances/1/edit'
  api.getInstance.mockImplementation(async (id: number) => ({ ...instance, id, name: id === 1 ? instance.name : '另一个实例' }))
  const wrapper = mount(InstanceList, { ...options, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  const firstEditor = wrapper.findComponent(InstanceEditor)
  const vm = firstEditor.vm as unknown as { form: { name: string }; save: () => Promise<void> }
  vm.form.name = '保留编辑内容'
  api.updateInstance.mockRejectedValueOnce(new Error('保存失败'))
  await vm.save()
  await flushPromises()
  expect(firstEditor.exists()).toBe(true)
  expect(vm.form.name).toBe('保留编辑内容')
  expect(document.querySelector('.ant-modal')?.textContent).toContain('保存失败')
  let finishSave!: (value: typeof instance) => void
  api.updateInstance.mockImplementationOnce(() => new Promise(resolve => { finishSave = resolve }))
  const pendingSave = vm.save()
  await flushPromises()
  state.route.path = '/database/instances/2/edit'
  await flushPromises()
  finishSave(instance)
  await pendingSave
  await flushPromises()
  const currentEditor = wrapper.findComponent(InstanceEditor)
  expect(currentEditor.exists()).toBe(true)
  expect((currentEditor.vm as unknown as { form: { name: string } }).form.name).toBe('另一个实例')
  expect(navigation.replace).not.toHaveBeenCalled()
  expect(api.updateInstance).toHaveBeenLastCalledWith(1, expect.objectContaining({ name: '保留编辑内容' }))
})
it('新建弹窗仍校验必填连接字段，不提交空表单', async () => {
  state.route.path = '/database/instances/new'
  const wrapper = mount(InstanceList, { ...options, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  const editor = wrapper.findComponent(InstanceEditor)
  const vm = editor.vm as unknown as { step: number; next: () => Promise<void> }
  await vm.next()
  await flushPromises()
  expect(vm.step).toBe(0)
  expect(document.querySelector('.ant-modal')?.textContent).toContain('请填写实例名称')
  expect(api.createInstance).not.toHaveBeenCalled()
})
it('弹窗保存后刷新台账，只提交可编辑字段和凭据引用', async () => {
  state.route.path = '/database/instances/1/edit'
  const wrapper = mount(InstanceList, { ...options, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  const editor = wrapper.findComponent(InstanceEditor)
  expect(editor.exists()).toBe(true)
  const vm = editor.vm as unknown as { form: { name: string }; next: () => Promise<void>; save: () => Promise<void> }
  vm.form.name = '更新实例'
  await vm.next()
  api.listInstances.mockClear()
  await vm.save()
  await flushPromises()
  expect(api.updateInstance).toHaveBeenCalledWith(1, expect.objectContaining({ name: '更新实例', credential_id: 1 }))
  const payload = api.updateInstance.mock.calls[0]![1]
  expect(payload).not.toHaveProperty('credential')
  expect(payload).not.toHaveProperty('password')
  expect(payload).not.toHaveProperty('app_ids')
  expect(api.listInstances).toHaveBeenCalled()
  expect(wrapper.findComponent(InstanceEditor).exists()).toBe(false)
})
it.each([
  { type: 'scan', operation: { type: 'scan', pattern: '*', cursor: 'signed-cursor', count: 25 } },
  { type: 'type', operation: { type: 'type', key: 'demo' } },
  { type: 'ttl', operation: { type: 'ttl', key: 'demo' } },
  { type: 'length', operation: { type: 'length', key: 'demo' } },
  { type: 'string_preview', operation: { type: 'string_preview', key: 'demo', offset: 25, length: 1024 } },
  { type: 'hash_page', operation: { type: 'hash_page', key: 'demo', cursor: 'signed-cursor', count: 25 } },
  { type: 'set_page', operation: { type: 'set_page', key: 'demo', cursor: 'signed-cursor', count: 25 } },
  { type: 'list_page', operation: { type: 'list_page', key: 'demo', offset: 25, count: 25 } },
  { type: 'zset_page', operation: { type: 'zset_page', key: 'demo', offset: 25, count: 25 } },
])('Redis $type请求仅携带该操作适用的分页字段', async ({ type, operation }) => {
  api.getInstance.mockResolvedValue({ ...instance, engine: 'redis', connection_options: { database: 0 } })
  const wrapper = mount(Workbench, options)
  wrappers.push(wrapper)
  await flushPromises()
  const page = wrapper.vm as unknown as {
    instanceId: number
    chooseInstance: () => Promise<void>
    redis: Record<string, unknown>
    execute: () => Promise<void>
  }
  page.instanceId = 1
  await page.chooseInstance()
  Object.assign(page.redis, { type, database: 0, key: 'demo', cursor: 'signed-cursor', offset: 25, count: 25 })
  api.submitTask.mockClear()
  await page.execute()
  expect(api.submitTask).toHaveBeenCalledWith({ kind: 'redis_read', instance_id: 1, context: { database: 0 }, operation }, expect.any(AbortSignal))
})
it.each([
  { confirmed: true, label: '停止已确认' },
  { confirmed: false, label: '停止待确认' },
])('停止依据默认收起，保留$label与原始证据', ({ confirmed, label }) => {
  const evidence = { confirmed, backend_ref: { connection_id: 12345 } }
  const wrapper = mount(TaskStatus, {
    props: { task: { ...task, status: 'succeeded', stop_confirmation: evidence } as TaskView },
    global: { plugins: [Antd] }
  })
  wrappers.push(wrapper)
  expect(wrapper.text()).toContain(label)
  const disclosure = wrapper.get('details')
  expect((disclosure.element as HTMLDetailsElement).open).toBe(false)
  expect(disclosure.get('summary').text()).toBe('查看停止依据')
  expect(JSON.parse(disclosure.get('pre').text())).toEqual(evidence)
})
it.each([
  { confirmed: true, label: '停止已确认' },
  { confirmed: false, label: '停止待确认' },
])('执行历史隐藏$label与停止依据，不读取结果集', async ({ confirmed, label }) => {
  const evidence = { confirmed, backend_ref: { connection_id: 12345 } }
  api.getTask.mockResolvedValue({ ...task, status: 'cancelled', stop_confirmation: evidence })
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  await (wrapper.vm as unknown as { choose: (id: number) => Promise<void> }).choose(31)
  await flushPromises()
  expect(wrapper.text()).not.toContain(label)
  expect(wrapper.find('details').exists()).toBe(false)
  expect(wrapper.text()).not.toContain('查看停止依据')
  await wrapper.findAll('.ant-tabs-tab').find(tab => tab.text().includes('过程'))!.trigger('click')
  await flushPromises()
  expect(wrapper.find('details').exists()).toBe(false)
  expect(wrapper.text()).not.toContain('12345')
  expect(api.getResult).not.toHaveBeenCalled()
})
it('仅维护权限行内连接测试终态成功后提示并刷新，不弹窗或读取查询策略及结果', async () => {
  state.permissions = new Set(['database:write'])
  api.getTask.mockResolvedValue({ ...task, kind: 'probe', status: 'succeeded', operation_summary: '只读检查通过', result_summary: { message: '连接和认证成功', read_only_checks: { verified: true } } })
  const success = vi.spyOn(message, 'success')
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  api.listInstances.mockClear()
  await (wrapper.vm as unknown as {
    testInstance: (id: number) => Promise<void>
  }).testInstance(1)
  await flushPromises()
  expect(api.submitTask).toHaveBeenCalledWith({ kind: 'probe', instance_id: 1 }, expect.any(AbortSignal), { silentBusinessError: true })
  expect(api.clientPolicy).not.toHaveBeenCalled()
  expect(api.getResult).not.toHaveBeenCalled()
  expect(success).toHaveBeenCalledWith(expect.stringContaining(`${instance.name}：数据库连接测试通过`))
  expect(success).toHaveBeenCalledWith(expect.stringContaining('连接和认证成功'))
  expect(api.listInstances).toHaveBeenCalledTimes(1)
  expect(document.querySelector('.ant-modal')).toBeNull()
  expect(document.body.textContent).not.toContain('只读检查通过')
  expect(document.body.textContent).not.toContain('read_only_checks')
  expect(wrapper.findAll('a').some(link => link.text() === '查询')).toBe(false)
})
const connectionTestButton = (wrapper: ReturnType<typeof mount>, id = 1) => wrapper
  .get(`tbody tr[data-row-key="${id}"]`).findAll('button').find(button => button.text().replace(/\s/g, '') === '测试')!

async function useRealProbeRequests(adapter: AxiosAdapter) {
  const actual = await vi.importActual<typeof import('@/api/database')>('@/api/database')
  api.submitTask.mockImplementation(actual.submitTask)
  api.getTask.mockImplementation(actual.getTask)
  http.defaults.adapter = adapter
}

it('连接测试queued和running期间仅本行loading且只显示spinner，等succeeded才提示通过并恢复测试图标', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const success = vi.spyOn(message, 'success')
  api.getTask.mockResolvedValueOnce({ ...task, kind: 'probe', status: 'queued' })
    .mockResolvedValueOnce({ ...task, kind: 'probe', status: 'running' })
    .mockResolvedValueOnce({ ...task, kind: 'probe', status: 'succeeded' })
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  api.listInstances.mockClear()
  await connectionTestButton(wrapper).trigger('click')
  await flushPromises()
  expect(connectionTestButton(wrapper).classes()).toContain('ant-btn-loading')
  expect(connectionTestButton(wrapper).findAll('.anticon-loading')).toHaveLength(1)
  expect(connectionTestButton(wrapper).find('.anticon-api').exists()).toBe(false)
  expect(document.querySelector('.ant-modal')).toBeNull()
  expect(success).not.toHaveBeenCalled()
  expect(api.listInstances).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  expect(connectionTestButton(wrapper).classes()).toContain('ant-btn-loading')
  expect(connectionTestButton(wrapper).findAll('.anticon')).toHaveLength(1)
  expect(connectionTestButton(wrapper).find('.anticon-api').exists()).toBe(false)
  expect(success).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  expect(success).toHaveBeenCalledTimes(1)
  expect(connectionTestButton(wrapper).classes()).not.toContain('ant-btn-loading')
  expect(connectionTestButton(wrapper).find('.anticon-loading').exists()).toBe(false)
  expect(connectionTestButton(wrapper).findAll('.anticon-api')).toHaveLength(1)
  expect(api.listInstances).toHaveBeenCalledTimes(1)
  expect(api.submitTask).toHaveBeenCalledTimes(1)
})
it.each([
  ['failed', '失败'], ['timed_out', '已超时'], ['queue_expired', '排队过期'], ['cancelled', '已取消'], ['rejected', '未执行']
])('连接测试终态%s给出失败与本人历史提示并刷新台账', async (status, label) => {
  const success = vi.spyOn(message, 'success'), failure = vi.spyOn(message, 'error')
  api.getTask.mockResolvedValue({ ...task, kind: 'probe', status, error_message: '安全诊断摘要' })
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  api.listInstances.mockClear()
  await connectionTestButton(wrapper).trigger('click')
  await flushPromises()
  expect(success).not.toHaveBeenCalled()
  expect(failure).toHaveBeenCalledWith(expect.stringContaining(instance.name))
  expect(failure).toHaveBeenCalledWith(expect.stringContaining(label))
  expect(failure).toHaveBeenCalledWith(expect.stringContaining('本人历史'))
  expect(failure).toHaveBeenCalledWith(expect.stringContaining('安全诊断摘要'))
  expect(connectionTestButton(wrapper).classes()).not.toContain('ant-btn-loading')
  expect(api.listInstances).toHaveBeenCalledTimes(1)
})
it.each([[403, 40301, '没有测试权限'], [429, 42901, '执行容量已满']] as const)('连接测试HTTP%s明确拒绝经真实拦截器后只提示一次、不重投', async (status, code, reason) => {
  const success = vi.spyOn(message, 'success'), failure = vi.spyOn(message, 'error')
  await useRealProbeRequests(async config => {
    throw new AxiosError('Request rejected', 'ERR_BAD_REQUEST', config, {}, {
      config, status, statusText: 'Rejected', headers: {}, data: { code, message: reason, data: null }
    })
  })
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  await connectionTestButton(wrapper).trigger('click')
  await flushPromises()
  expect(success).not.toHaveBeenCalled()
  expect(failure).toHaveBeenCalledTimes(1)
  expect(failure).toHaveBeenCalledWith(expect.stringContaining(reason))
  expect(failure).toHaveBeenCalledWith(expect.stringContaining('本人历史'))
  expect(api.submitTask).toHaveBeenCalledTimes(1)
  expect(api.getTask).not.toHaveBeenCalled()
  expect(connectionTestButton(wrapper).classes()).not.toContain('ant-btn-loading')
})
it('连接测试拒绝携带任务ID经真实拦截器后仍等终态，再给出唯一提示', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const success = vi.spyOn(message, 'success'), failure = vi.spyOn(message, 'error')
  let reads = 0
  await useRealProbeRequests(async config => {
    if (config.method === 'post') {
      throw new AxiosError('Request rejected', 'ERR_BAD_REQUEST', config, {}, {
        config, status: 429, statusText: 'Rejected', headers: {},
        data: { code: 42901, message: '执行容量已满', data: { task_id: 31 } }
      })
    }
    return { config, status: 200, statusText: 'OK', headers: {}, data: { code: 0, data: {
      ...task, kind: 'probe', status: reads++ === 0 ? 'queued' : 'rejected', error_message: '执行容量已满'
    } } }
  })
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  await connectionTestButton(wrapper).trigger('click')
  await flushPromises()
  expect(failure).not.toHaveBeenCalled()
  expect(connectionTestButton(wrapper).classes()).toContain('ant-btn-loading')
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  expect(success).not.toHaveBeenCalled()
  expect(failure).toHaveBeenCalledTimes(1)
  expect(failure).toHaveBeenCalledWith(expect.stringContaining('未执行'))
  expect(failure).toHaveBeenCalledWith(expect.stringContaining('本人历史'))
  expect(api.submitTask).toHaveBeenCalledTimes(1)
})
it.each([
  ['网络中断', new AxiosError('Network Error', 'ERR_NETWORK', undefined, {})],
  ['网关异常', new AxiosError('Bad Gateway', 'ERR_BAD_RESPONSE', undefined, {}, {
    status: 502, statusText: 'Bad Gateway', headers: {}, data: 'Bad Gateway', config: { headers: new AxiosHeaders() }
  })]
])('%s连接测试提交未知锁住该行，提示本人历史核对且不自动或重复POST', async (_kind, failure) => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const success = vi.spyOn(message, 'success'), warning = vi.spyOn(message, 'warning')
  api.submitTask.mockRejectedValueOnce(failure)
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  await connectionTestButton(wrapper).trigger('click')
  await flushPromises()
  expect(success).not.toHaveBeenCalled()
  expect(warning).toHaveBeenCalledWith(expect.stringContaining('提交结果未知'))
  expect(warning).toHaveBeenCalledWith(expect.stringContaining('本人历史'))
  expect(connectionTestButton(wrapper).attributes('disabled')).toBeDefined()
  await (wrapper.vm as unknown as { testInstance: (id: number) => Promise<void> }).testInstance(1)
  await vi.advanceTimersByTimeAsync(10000)
  expect(api.submitTask).toHaveBeenCalledTimes(1)
  expect(api.getTask).not.toHaveBeenCalled()
})
it('连接测试按实例独立跟踪，阻止同行重复提交，刷新与另一行测试不丢失原任务', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const success = vi.spyOn(message, 'success'), failure = vi.spyOn(message, 'error')
  const second = { ...instance, id: 2, name: '第二数据库' }
  api.listInstances.mockResolvedValue({ items: [instance, second], total: 2 })
  api.submitTask.mockImplementation(async ({ instance_id }) => ({ task_id: instance_id === 1 ? 31 : 32, status: 'queued' }))
  const statuses: Record<number, string> = { 31: 'queued', 32: 'running' }
  api.getTask.mockImplementation(async (id: number) => ({ ...task, task_id: id, instance_id: id === 31 ? 1 : 2, kind: 'probe', status: statuses[id] }))
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  const vm = wrapper.vm as unknown as { testInstance: (id: number) => Promise<void>; load: () => Promise<void> }
  await vm.testInstance(1)
  await vm.testInstance(1)
  await vm.testInstance(2)
  expect(api.submitTask).toHaveBeenCalledTimes(2)
  api.listInstances.mockResolvedValue({ items: [{ ...second }, { ...instance }], total: 2 })
  await vm.load()
  await flushPromises()
  expect(connectionTestButton(wrapper, 1).classes()).toContain('ant-btn-loading')
  expect(connectionTestButton(wrapper, 2).classes()).toContain('ant-btn-loading')
  statuses[31] = 'succeeded'
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  expect(success).toHaveBeenCalledWith(expect.stringContaining(`${instance.name}：数据库连接测试通过`))
  expect(success).not.toHaveBeenCalledWith(expect.stringContaining('第二数据库'))
  expect(connectionTestButton(wrapper, 1).classes()).not.toContain('ant-btn-loading')
  expect(connectionTestButton(wrapper, 2).classes()).toContain('ant-btn-loading')
  statuses[32] = 'failed'
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  expect(failure).toHaveBeenCalledWith(expect.stringContaining('第二数据库'))
  expect(connectionTestButton(wrapper, 2).classes()).not.toContain('ant-btn-loading')
  expect(api.submitTask).toHaveBeenCalledTimes(2)
})
it('连接测试临时读取失败只重试GET，loading保留并提示本人历史核对', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const success = vi.spyOn(message, 'success'), warning = vi.spyOn(message, 'warning')
  api.getTask.mockRejectedValueOnce(new AxiosError('Network Error', 'ERR_NETWORK', undefined, {}))
    .mockResolvedValueOnce({ ...task, kind: 'probe', status: 'succeeded' })
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  await connectionTestButton(wrapper).trigger('click')
  await flushPromises()
  expect(success).not.toHaveBeenCalled()
  expect(warning).toHaveBeenCalledWith(expect.stringContaining('本人历史'))
  expect(connectionTestButton(wrapper).classes()).toContain('ant-btn-loading')
  await vi.advanceTimersByTimeAsync(3000)
  await flushPromises()
  expect(success).toHaveBeenCalledTimes(1)
  expect(api.submitTask).toHaveBeenCalledTimes(1)
  expect(api.getTask).toHaveBeenCalledTimes(2)
})
it('连接测试HTTP202受理后读取权限丢失经真实拦截器只提示一次，不重投任务', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const success = vi.spyOn(message, 'success'), warning = vi.spyOn(message, 'warning'), failure = vi.spyOn(message, 'error')
  await useRealProbeRequests(async config => {
    if (config.method === 'post') {
      return { config, status: 202, statusText: 'Accepted', headers: {},
        data: { code: 0, data: { task_id: 31, status: 'queued' } } }
    }
    throw new AxiosError('Request rejected', 'ERR_BAD_REQUEST', config, {}, {
      config, status: 403, statusText: 'Rejected', headers: {},
      data: { code: 40301, message: '读取任务权限已失效', data: null }
    })
  })
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  await connectionTestButton(wrapper).trigger('click')
  await flushPromises()
  expect(success).not.toHaveBeenCalled()
  expect(failure).not.toHaveBeenCalled()
  expect(warning).toHaveBeenCalledTimes(1)
  expect(warning).toHaveBeenCalledWith(expect.stringContaining('本人历史'))
  expect(connectionTestButton(wrapper).attributes('disabled')).toBeDefined()
  await vi.advanceTimersByTimeAsync(10000)
  expect(api.getTask).toHaveBeenCalledTimes(1)
  expect(api.submitTask).toHaveBeenCalledTimes(1)
})
it.each(['submitTask', 'getTask'] as const)('连接测试%s在途卸载后中止客户端请求，不弹迟到提示或刷新', async pendingApi => {
  const success = vi.spyOn(message, 'success'), failure = vi.spyOn(message, 'error'), warning = vi.spyOn(message, 'warning')
  let finish!: (value: typeof task) => void
  api[pendingApi].mockImplementationOnce(() => new Promise<typeof task>(resolve => { finish = resolve }))
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  await connectionTestButton(wrapper).trigger('click')
  await flushPromises()
  const signal = api[pendingApi].mock.calls[0]![1] as AbortSignal
  api.listInstances.mockClear()
  wrapper.unmount()
  expect(signal.aborted).toBe(true)
  finish({ ...task, kind: 'probe', status: 'succeeded' })
  await flushPromises()
  expect(success).not.toHaveBeenCalled()
  expect(failure).not.toHaveBeenCalled()
  expect(warning).not.toHaveBeenCalled()
  expect(api.listInstances).not.toHaveBeenCalled()
  if (pendingApi === 'submitTask') expect(api.getTask).not.toHaveBeenCalled()
})
it('连接测试入口校验维护权限，只有查询权限也不能直接调用提交', async () => {
  state.permissions = new Set(['database:query'])
  const wrapper = mount(InstanceList, options)
  wrappers.push(wrapper)
  await flushPromises()
  expect(wrapper.findAll('tbody button').some(button => button.text() === '测试')).toBe(false)
  await (wrapper.vm as unknown as { testInstance: (id: number) => Promise<void> }).testInstance(1)
  expect(api.submitTask).not.toHaveBeenCalled()
})
it('保存结果按序号保留重名列，特殊值和HTML以纯文本展示', async () => {
  const wrapper = mount(ResultTable, { props: { result }, global: { plugins: [Antd] } })
  wrappers.push(wrapper)
  await flushPromises()
  expect(wrapper.findAll('thead th').map(th => th.text())).toEqual(['id', 'id'])
  expect(wrapper.text()).toContain('9007199254740993 [int]')
  expect(wrapper.text()).toContain('<b>原始数据</b>')
  expect(wrapper.find('tbody b').exists()).toBe(false)
})
it('执行审计详情仅消费审计API，不读取任务或结果集', async () => {
  const audit = {
    ...task,
    id: 31,
    actor_id: 8,
    actor_name: '审计对象',
    source_ip: '192.0.2.1',
    events: [{ id: 1, event_type: 'submitted', created_at: task.created_at }]
  }
  api.listAudit.mockResolvedValue({ items: [audit], total: 1 })
  api.getAudit.mockResolvedValue(audit)
  const wrapper = mount(Records, { ...options, props: { audit: true } })
  wrappers.push(wrapper)
  await flushPromises()
  await (wrapper.vm as unknown as {
    choose: (id: number) => Promise<void>
  }).choose(31)
  await flushPromises()
  expect(wrapper.text()).toContain('审计对象')
  await wrapper.findAll('.ant-tabs-tab').find(tab => tab.text() === '过程事件')!.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('submitted')
  expect(api.getTask).not.toHaveBeenCalled()
  expect(api.getResult).not.toHaveBeenCalled()
  expect(wrapper.findAll('button').some(button => button.text() === '查看结果')).toBe(false)
})
it('执行审计采用历史同款紧凑筛选、全宽真实字段表格和关闭状态的右侧抽屉', async () => {
  api.listAudit.mockResolvedValue({ items: [auditRecord], total: 1 })
  const wrapper = mount(Records, { ...options, props: { audit: true } })
  wrappers.push(wrapper)
  await flushPromises()
  expect(wrapper.find('.audit-page').exists()).toBe(true)
  expect(wrapper.find('.db-records-layout').exists()).toBe(false)
  expect(wrapper.find('.db-card').exists()).toBe(false)
  expect(wrapper.get('.audit-toolbar').findAll('button').map(button => button.text().replace(/\s/g, ''))).toEqual(['更多筛选'])
  const table = wrapper.findComponent(Table)
  expect(table.props('bordered')).toBe(true)
  expect(table.props('columns')?.map(column => column.title)).toEqual(['操作编号', '操作者 / 来源 IP', '实例 / 上下文', '操作摘要', '提交时间', '状态', '操作'])
  expect(wrapper.text()).toContain('数据库审计对象')
  expect(wrapper.text()).toContain('192.0.2.18')
  expect(wrapper.text()).toContain('sales')
  expect(wrapper.text()).toContain('查看详情')
  expect(wrapper.findComponent({ name: 'ADrawer' }).props('open')).toBe(false)
  expect(api.getAudit).not.toHaveBeenCalled()
})
it('执行审计默认最近30天单RangePicker，结束参数覆盖所选秒，完整范围与快捷和清空即查', async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-11-09T13:14:15.678+08:00'))
  const wrapper = mount(Records, { ...options, props: { audit: true } })
  wrappers.push(wrapper)
  await flushPromises()
  expect(wrapper.findAll('input[type="datetime-local"]')).toHaveLength(0)
  const picker = wrapper.getComponent({ name: 'ARangePicker' })
  const range = [dayjs().subtract(30, 'day').format('YYYY-MM-DD HH:mm:ss'), dayjs().format('YYYY-MM-DD HH:mm:ss')]
  expect(picker.props('value')).toEqual(range)
  expect(picker.props('showTime')).toBe(true)
  expect(picker.props('presets').map((preset: { label: string }) => preset.label)).toEqual(['最近 7 天', '最近 30 天', '最近 90 天'])
  expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, started_from: dayjs(range[0]).toISOString(), started_to: dayjs(range[1]).add(1, 'second').toISOString() }))
  picker.vm.$emit('calendarChange', ['2026-01-01 00:00:01', null])
  await flushPromises()
  expect(api.listAudit).toHaveBeenCalledTimes(1)
  const page = wrapper.vm as unknown as { query: { page: number } }
  const preset = picker.props('presets')[0].value.map((value: dayjs.Dayjs) => value.format('YYYY-MM-DD HH:mm:ss'))
  for (const selected of [['2026-01-01 00:00:01', '2026-01-02 00:00:02'], preset, null]) {
    page.query.page = 3
    const calls = api.listAudit.mock.calls.length
    picker.vm.$emit('update:value', selected)
    picker.vm.$emit('change', selected)
    await flushPromises()
    expect(api.listAudit).toHaveBeenCalledTimes(calls + 1)
    expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, started_from: selected ? dayjs(selected[0]).toISOString() : undefined, started_to: selected ? dayjs(selected[1]).add(1, 'second').toISOString() : undefined }))
  }
})
it.each([{ index: 0, key: 'engine', value: 'redis' }, { index: 1, key: 'kind', value: 'sql' }, { index: 2, key: 'status', value: 'succeeded' }])('执行审计$key选择与清空立即回第一页查询', async ({ index, key, value }) => {
  const wrapper = mount(Records, { ...options, props: { audit: true } })
  wrappers.push(wrapper)
  await flushPromises()
  const page = wrapper.vm as unknown as { query: { page: number } }
  const select = wrapper.get('.audit-toolbar').findAllComponents(Select)[index]!
  for (const selected of [value, undefined]) {
    page.query.page = 4
    const calls = api.listAudit.mock.calls.length
    select.vm.$emit('update:value', selected)
    select.vm.$emit('change', selected)
    await flushPromises()
    expect(api.listAudit).toHaveBeenCalledTimes(calls + 1)
    expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, [key]: selected }))
  }
})
it('执行审计关键词仅回车提交或清空图标立即移除，分页和下拉不提交草稿', async () => {
  const wrapper = mount(Records, { ...options, props: { audit: true } })
  wrappers.push(wrapper)
  await flushPromises()
  const input = wrapper.get('input[aria-label="操作者 / 实例关键词"]')
  expect(input.attributes('maxlength')).toBe('255')
  await input.setValue('  操作者 %_  ')
  expect(api.listAudit).toHaveBeenCalledTimes(1)
  await input.trigger('keydown', { key: 'Enter', keyCode: 13 })
  await flushPromises()
  expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, keyword: '操作者 %_' }))
  await input.setValue('未提交关键词')
  wrapper.findComponent(Table).vm.$emit('change', { current: 2, pageSize: 20 })
  await flushPromises()
  expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2, keyword: '操作者 %_' }))
  await wrapper.get('.audit-toolbar .ant-input-clear-icon').trigger('click')
  await flushPromises()
  expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, keyword: undefined }))
  expect(api.listTasks).not.toHaveBeenCalled()
})
it('执行审计更多筛选保持精确参数，每项回车提交且清空即查，不随其他筛选提交草稿', async () => {
  const wrapper = mount(Records, { ...options, props: { audit: true } })
  wrappers.push(wrapper)
  await flushPromises()
  const toggle = wrapper.get('.audit-more-toggle')
  expect(toggle.attributes('aria-expanded')).toBe('false')
  await toggle.trigger('click')
  expect(toggle.attributes('aria-expanded')).toBe('true')
  const advanced = wrapper.get('.audit-advanced')
  const ids = advanced.findAllComponents({ name: 'AInputNumber' })
  const source = advanced.get('input[aria-label="来源 IP"]'), database = advanced.get('input[aria-label="Database 上下文"]')
  ids[0]!.vm.$emit('update:value', 12)
  ids[0]!.vm.$emit('change', 12)
  ids[1]!.vm.$emit('update:value', 18)
  ids[1]!.vm.$emit('change', 18)
  await source.setValue('  192.0.2.18  ')
  await database.setValue('  sales  ')
  expect(api.listAudit).toHaveBeenCalledTimes(1)
  const engine = wrapper.get('.audit-toolbar').findAllComponents(Select)[0]!
  engine.vm.$emit('update:value', 'mysql'); engine.vm.$emit('change', 'mysql')
  await flushPromises()
  expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ instance_id: undefined, actor_id: undefined, source_ip: undefined, database: undefined }))
  for (const [index, key, value] of [[0, 'instance_id', 12], [1, 'actor_id', 18]] as const) {
    ids[index]!.vm.$emit('pressEnter')
    await flushPromises()
    expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, [key]: value }))
    ids[index]!.vm.$emit('update:value', null); ids[index]!.vm.$emit('change', null)
    await flushPromises()
    expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ [key]: undefined }))
  }
  await source.trigger('keydown', { key: 'Enter', keyCode: 13 })
  await flushPromises()
  expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ source_ip: '192.0.2.18', database: undefined }))
  await database.trigger('keydown', { key: 'Enter', keyCode: 13 })
  await flushPromises()
  expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ database: 'sales' }))
  await advanced.findAll('.ant-input-clear-icon')[0]!.trigger('click')
  await flushPromises()
  expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, source_ip: undefined, database: 'sales' }))
  const beforeEdit = api.listAudit.mock.calls.length
  await database.setValue('尚未提交的新库名')
  expect(api.listAudit).toHaveBeenCalledTimes(beforeEdit)
  await database.setValue('')
  await flushPromises()
  expect(api.listAudit).toHaveBeenCalledTimes(beforeEdit + 1)
  expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, database: undefined }))
})
it('执行审计抽屉保留概览、事件全部证据及两个安全快照，永不提供结果入口', async () => {
  api.listAudit.mockResolvedValue({ items: [auditRecord], total: 1 }); api.getAudit.mockResolvedValue(auditRecord)
  const wrapper = mount(Records, { ...options, props: { audit: true } }); wrappers.push(wrapper)
  await flushPromises()
  await wrapper.get('.audit-detail-link').trigger('click'); await flushPromises()
  const drawer = wrapper.findComponent({ name: 'ADrawer' })
  expect(drawer.props('open')).toBe(true)
  expect(drawer.props('width')).toBe('min(780px, 100vw)')
  expect(wrapper.text()).toContain('SELECT 1;'); expect(wrapper.text()).toContain('原始提交')
  expect(wrapper.text()).toContain('实际执行'); expect(wrapper.text()).toContain('结果摘要')
  expect(wrapper.findAll('.ant-tabs-tab').map(tab => tab.text())).toEqual(['概览', '过程事件', '安全快照'])
  await wrapper.findAll('.ant-tabs-tab').find(tab => tab.text() === '过程事件')!.trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('只读校验事实'); expect(wrapper.text()).toContain('driver')
  expect(wrapper.text()).toContain('AUDIT_EVENT_CODE'); expect(wrapper.text()).toContain('审计事件说明')
  await wrapper.findAll('.ant-tabs-tab').find(tab => tab.text() === '安全快照')!.trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('目标快照'); expect(wrapper.text()).toContain('只读凭据快照')
  expect(wrapper.text()).not.toContain('查看结果')
  expect(api.getTask).not.toHaveBeenCalled(); expect(api.getResult).not.toHaveBeenCalled(); expect(api.submitTask).not.toHaveBeenCalled()
})
it('执行审计详情立即打开加载状态，失败留在抽屉并可重试，筛选与分页不关闭详情', async () => {
  let reject!: (error: Error) => void
  api.getAudit.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail })).mockResolvedValue(auditRecord)
  const wrapper = mount(Records, { ...options, props: { audit: true } }); wrappers.push(wrapper)
  await flushPromises()
  const page = wrapper.vm as unknown as { choose: (id: number) => Promise<void> }
  const pending = page.choose(31); await flushPromises()
  expect(wrapper.findComponent({ name: 'ADrawer' }).props('open')).toBe(true)
  expect(wrapper.find('.audit-detail-loading').exists()).toBe(true)
  reject(new Error('审计详情暂不可用')); await pending; await flushPromises()
  expect(wrapper.get('.audit-detail-error').text()).toContain('审计详情暂不可用')
  await wrapper.get('.audit-detail-retry').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('数据库审计对象')
  wrapper.findComponent(Table).vm.$emit('change', { current: 2, pageSize: 20 }); await flushPromises()
  expect(wrapper.findComponent({ name: 'ADrawer' }).props('open')).toBe(true)
  expect(api.getAudit).toHaveBeenCalledTimes(2)
})
it('执行审计切换记录和关闭抽屉后，迟到详情不回写且旧事实立即清空', async () => {
  const finishes: ((value: typeof auditRecord) => void)[] = []
  api.getAudit.mockImplementation(() => new Promise(resolve => { finishes.push(resolve) }))
  const wrapper = mount(Records, { ...options, props: { audit: true } }); wrappers.push(wrapper)
  await flushPromises()
  const page = wrapper.vm as unknown as { choose: (id: number) => Promise<void>; closeDetails: () => void }
  const first = page.choose(31), second = page.choose(32)
  finishes[1]!({ ...auditRecord, task_id: 32, actor_name: '新审计对象' }); await second; await flushPromises()
  finishes[0]!({ ...auditRecord, actor_name: '迟到旧审计对象' }); await first; await flushPromises()
  expect(wrapper.text()).toContain('新审计对象'); expect(wrapper.text()).not.toContain('迟到旧审计对象')
  const third = page.choose(33); await flushPromises()
  expect(wrapper.text()).not.toContain('新审计对象')
  page.closeDetails()
  finishes[2]!({ ...auditRecord, actor_name: '关闭后迟到审计对象' }); await third; await flushPromises()
  expect(wrapper.findComponent({ name: 'ADrawer' }).props('open')).toBe(false)
  expect(wrapper.text()).not.toContain('关闭后迟到审计对象')
})
it('执行审计权限撤销及详情403会清除列表抽屉，并阻止在途详情回写', async () => {
  state.permissions = reactive(new Set(['database:audit']))
  api.listAudit.mockResolvedValue({ items: [auditRecord], total: 1 })
  let finish!: (value: typeof auditRecord) => void
  api.getAudit.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const wrapper = mount(Records, { ...options, props: { audit: true } }); wrappers.push(wrapper)
  await flushPromises()
  const page = wrapper.vm as unknown as { choose: (id: number) => Promise<void> }
  const pending = page.choose(31)
  state.permissions.delete('database:audit'); await flushPromises()
  finish(auditRecord); await pending; await flushPromises()
  expect(wrapper.text()).not.toContain('数据库审计对象')
  expect(wrapper.findComponent({ name: 'ADrawer' }).props('open')).toBe(false)
  state.permissions.add('database:audit'); await flushPromises()
  api.getAudit.mockRejectedValueOnce(new ApiError(40301, '审计详情权限失效'))
  await page.choose(31); await flushPromises()
  expect(wrapper.text()).not.toContain('数据库审计对象')
  expect(wrapper.text()).toContain('审计详情权限失效')
  expect(wrapper.findComponent({ name: 'ADrawer' }).props('open')).toBe(false)
})
it('执行审计离开路由使在途详情失效，重新进入不展示旧操作', async () => {
  state.route.path = '/database/audit'
  let finish!: (value: typeof auditRecord) => void
  api.getAudit.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const wrapper = mount(Records, { ...options, props: { audit: true } }); wrappers.push(wrapper)
  await flushPromises()
  const pending = (wrapper.vm as unknown as { choose: (id: number) => Promise<void> }).choose(31)
  state.route.path = '/database/workbench'
  await flushPromises()
  finish(auditRecord); await pending; await flushPromises()
  expect(wrapper.findComponent({ name: 'ADrawer' }).props('open')).toBe(false)
  expect(wrapper.text()).not.toContain('数据库审计对象')
})
it('执行历史重设计：列表先展示摘要，选中记录后打开详情且不预取结果', async () => {
  api.listTasks.mockResolvedValue({ items: [{ ...task, status: 'succeeded' }], total: 1 })
  api.getTask.mockResolvedValue({ ...task, status: 'succeeded' })
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  const headers = wrapper.findAll('thead th').map(header => header.text())
  expect(headers).toContain('执行耗时')
  expect(headers).toContain('结果')
  expect(wrapper.find('pre').exists()).toBe(false)
  await wrapper.findAll('a').find(link => link.text() === '#31')!.trigger('click')
  await flushPromises()
  expect(wrapper.findComponent({ name: 'ADrawer' }).props('open')).toBe(true)
  expect(wrapper.text()).toContain('SELECT 1;')
  expect(api.getResult).not.toHaveBeenCalled()
})
it('执行历史使用紧凑工具栏与直接边框表格，移除查询重置刷新按钮并保留筛选及工作台入口', async () => {
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  expect(wrapper.get('.op-hero').text()).not.toContain('仅本人可见')
  expect(wrapper.get('.history-hero-actions').text()).toContain('打开查询工作台')
  expect(wrapper.find('.history-list-heading').exists()).toBe(false)
  expect(wrapper.find('.db-card').exists()).toBe(false)
  expect(wrapper.findComponent(Table).props('bordered')).toBe(true)
  expect(wrapper.findComponent(Table).props('size')).not.toBe('small')
  const toolbar = wrapper.get('.history-toolbar')
  expect(toolbar.findComponent({ name: 'AInputNumber' }).exists()).toBe(false)
  expect(toolbar.findAllComponents(Select)).toHaveLength(3)
  expect(toolbar.findAll('input[type="datetime-local"]')).toHaveLength(0)
  expect(toolbar.findAll('button')).toHaveLength(0)
  const keyword = toolbar.get('input[aria-label="搜索执行历史"]')
  expect(keyword.attributes('maxlength')).toBe('255')
  const picker = toolbar.getComponent({ name: 'ARangePicker' })
  expect(picker.props('showTime')).toBe(true)
  expect(picker.props('valueFormat')).toBe('YYYY-MM-DD HH:mm:ss')
  expect(picker.props('presets').map((item: { label: string }) => item.label)).toEqual(['最近 7 天', '最近 30 天', '最近 90 天'])
})
it('执行历史关键词搜索只请求服务端，分页结果不在前端按关键词二次过滤', async () => {
  api.listTasks.mockResolvedValue({ items: [{ ...task, status: 'succeeded' }], total: 81 })
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  const toolbar = wrapper.get('.history-toolbar')
  await toolbar.get('input[aria-label="搜索执行历史"]').setValue('服务端命中的字段')
  await toolbar.get('input[aria-label="搜索执行历史"]').trigger('keydown', { key: 'Enter', keyCode: 13 })
  await flushPromises()
  expect(api.listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ keyword: '服务端命中的字段', page: 1 }))
  expect(wrapper.findComponent(Table).props('dataSource')).toHaveLength(1)
  expect(wrapper.findComponent(Table).props('pagination')).toEqual(expect.objectContaining({ total: 81 }))
  expect(wrapper.text()).toContain('共享MySQL')
  wrapper.findComponent(Table).vm.$emit('change', { current: 2, pageSize: 20 })
  await flushPromises()
  expect(api.listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ keyword: '服务端命中的字段', page: 2 }))
})
it('执行历史实际API将关键词和范围传给tasks端点，不在客户端解释通配符', async () => {
  const actual = await vi.importActual<typeof import('@/api/database')>('@/api/database')
  const adapter = vi.fn<AxiosAdapter>(async config => ({
    data: { code: 0, message: 'success', data: { items: [], total: 0 } },
    status: 200, statusText: 'OK', headers: new AxiosHeaders(), config,
  }))
  http.defaults.adapter = adapter
  await actual.listTasks({ keyword: 'SELECT %_', started_from: '2026-01-01T00:00:01Z', started_to: '2026-01-02T00:00:02Z' })
  expect(adapter).toHaveBeenCalledWith(expect.objectContaining({ url: '/database/tasks', params: { keyword: 'SELECT %_', started_from: '2026-01-01T00:00:01Z', started_to: '2026-01-02T00:00:02Z' } }))
})
it('执行历史列表与详情结果行数固定en-US千位分隔', async () => {
  const format = vi.spyOn(Number.prototype, 'toLocaleString')
  const completed = { ...task, status: 'succeeded', result_summary: { returned_rows: 12345, returned_bytes: 128 } }
  api.listTasks.mockResolvedValue({ items: [completed], total: 1 })
  api.getTask.mockResolvedValue(completed)
  try {
    const wrapper = mount(Records, { ...options, props: { audit: false } })
    wrappers.push(wrapper)
    await flushPromises()
    expect(wrapper.get('.history-result-title').text()).toContain('12,345')
    expect(format).toHaveBeenCalledWith('en-US')
    format.mockClear()
    await (wrapper.vm as unknown as { choose: (id: number) => Promise<void> }).choose(31)
    await flushPromises()
    expect(wrapper.get('.history-detail-stats').text()).toContain('12,345')
    expect(format).toHaveBeenCalledWith('en-US')
  } finally {
    format.mockRestore()
  }
})
it('执行历史清空时间范围后移除时间参数，审计使用独立范围和精确实例筛选', async () => {
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  const picker = wrapper.getComponent({ name: 'ARangePicker' })
  picker.vm.$emit('update:value', ['2026-01-01 00:00:01', '2026-01-02 00:00:02'])
  picker.vm.$emit('change', ['2026-01-01 00:00:01', '2026-01-02 00:00:02'])
  await flushPromises()
  picker.vm.$emit('update:value', null)
  picker.vm.$emit('change', null)
  await flushPromises()
  expect(api.listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ started_from: undefined, started_to: undefined }))
  const page = wrapper.vm as unknown as { query: { instance_id: number; keyword: string } }
  Object.assign(page.query, { instance_id: 12, keyword: '审计关键字' })
  await wrapper.setProps({ audit: true })
  await flushPromises()
  const auditPicker = wrapper.getComponent({ name: 'ARangePicker' })
  expect(auditPicker.props('value')).not.toBeNull()
  expect(wrapper.findAll('input[type="datetime-local"]')).toHaveLength(0)
  const range = ['2026-01-03 00:00:01', '2026-01-04 00:00:02']
  auditPicker.vm.$emit('update:value', range)
  auditPicker.vm.$emit('change', range)
  await flushPromises()
  expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ instance_id: 12, keyword: '审计关键字', started_from: dayjs(range[0]).toISOString(), started_to: dayjs(range[1]).add(1, 'second').toISOString() }))
})
it.each([390, 760, 768, 1280])('执行历史概览元数据在 %ipx 按窄屏单列、桌面双列展示时间', async (width) => {
  const media = vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => {
    const minimum = query.match(/min-width:\s*(\d+)px/)
    const maximum = query.match(/max-width:\s*(\d+)px/)
    return {
      matches: minimum ? width >= Number(minimum[1]) : maximum ? width <= Number(maximum[1]) : false,
      media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
      addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
    }
  })
  try {
    api.getTask.mockResolvedValue({ ...task, status: 'succeeded', started_at: '2026-01-01T00:00:01Z', finished_at: '2026-01-01T00:00:02Z' })
    const wrapper = mount(Records, { ...options, props: { audit: false } })
    wrappers.push(wrapper)
    await (wrapper.vm as unknown as { choose: (id: number) => Promise<void> }).choose(31)
    await flushPromises()
    const rows = wrapper.get('.history-metadata').findAll('.ant-descriptions-row')
    const startRow = rows.find(row => row.text().includes('开始时间'))!
    const endRow = rows.find(row => row.text().includes('结束时间'))!
    expect(startRow.element === endRow.element).toBe(width >= 768)
    expect(rows).toHaveLength(width >= 768 ? 3 : 5)
    expect(rows.find(row => row.text().includes('上下文'))!.findAll('td')).toHaveLength(1)
  } finally {
    media.mockRestore()
  }
})
it('执行历史重设计：关闭抽屉清空结果并阻止在途结果回写', async () => {
  let finish!: (value: typeof result) => void
  api.getTask.mockResolvedValue({ ...task, status: 'succeeded' })
  api.getResult.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  const page = wrapper.vm as unknown as { choose: (id: number) => Promise<void>; viewResult: () => Promise<void>; closeDetails: () => void }
  await page.choose(31)
  const pending = page.viewResult()
  await flushPromises()
  expect(typeof page.closeDetails).toBe('function')
  page.closeDetails()
  finish({ ...result, rows: [['关闭后不可回写的敏感结果']] })
  await pending
  await flushPromises()
  expect(wrapper.findComponent(ResultTable).exists()).toBe(false)
  expect(wrapper.text()).not.toContain('关闭后不可回写')
  expect(wrapper.findComponent({ name: 'ADrawer' }).props('open')).toBe(false)
})
it.each(['stopping', 'recovering'])('执行历史$status仅展示真实时间节点，保留用户停止警告但隐藏技术停止字段和原始依据', async status => {
  api.getTask.mockResolvedValue({ ...task, status, started_at: '2026-01-01T00:00:01Z', stop_confirmation: { confirmed: false, source: 'driver', backend_ref: { connection_id: 12345 } }, stop_reason: 'execution_timeout', error_code: 'stop_check_pending', error_message: '停止状态待核对' })
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await (wrapper.vm as unknown as { choose: (id: number) => Promise<void> }).choose(31)
  await flushPromises()
  const tab = wrapper.findAll('.ant-tabs-tab').find(item => item.text().includes('过程'))
  expect(tab).toBeDefined()
  await tab!.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('提交请求')
  expect(wrapper.text()).toContain('开始执行')
  expect(wrapper.get('.history-feedback').text()).toContain('已申请停止，目标停止状态尚未确认；不会自动重新执行。')
  expect(wrapper.text()).toContain('停止状态待核对')
  expect(wrapper.text()).not.toContain('停止原因')
  expect(wrapper.text()).not.toContain('错误代码')
  expect(wrapper.text()).not.toContain('execution_timeout')
  expect(wrapper.text()).not.toContain('stop_check_pending')
  expect(wrapper.text()).not.toContain('查看停止依据')
  expect(wrapper.text()).not.toContain('12345')
  expect(wrapper.text()).not.toContain('查询校验通过')
  expect(wrapper.text()).not.toContain('执行完成')
})
it('执行历史重设计：结果翻页显示加载并在撤权后移除结果', async () => {
  let reject!: (reason: unknown) => void
  api.getTask.mockResolvedValue({ ...task, status: 'succeeded' })
  api.getResult.mockResolvedValueOnce(result).mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail }))
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  const page = wrapper.vm as unknown as { choose: (id: number) => Promise<void>; viewResult: () => Promise<void> }
  await page.choose(31)
  await page.viewResult()
  await flushPromises()
  const table = wrapper.findComponent(ResultTable)
  table.vm.$emit('page', 2, 50)
  await flushPromises()
  expect(wrapper.findComponent(ResultTable).props('loading')).toBe(true)
  reject(new ApiError(40302, '实例查询权限已撤销', null))
  await flushPromises()
  expect(wrapper.findComponent(ResultTable).exists()).toBe(false)
  expect(wrapper.text()).not.toContain('9007199254740993')
  expect(wrapper.text()).toContain('实例查询权限已撤销')
})
it('执行历史重设计：同路由 task_id 切换自动选中新任务，旧详情不可回写', async () => {
  let old!: (value: typeof task) => void
  state.route.query = { task_id: '31' }
  api.getTask.mockImplementation(id => id === 31 ? new Promise(resolve => { old = resolve }) : Promise.resolve({ ...task, task_id: 32, status: 'succeeded', submitted_text: 'SELECT 32' }))
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  state.route.query = { task_id: '32' }
  await flushPromises()
  expect(api.getTask).toHaveBeenCalledWith(32, expect.any(AbortSignal))
  old({ ...task, submitted_text: '旧任务敏感文本' })
  await flushPromises()
  expect(wrapper.text()).toContain('SELECT 32')
  expect(wrapper.text()).not.toContain('旧任务敏感文本')
})
it('执行历史首次进入默认最近30天完整本地时间，ISO结束传参加1秒且不调用实例管理接口', async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-11-09T13:14:15.678+08:00'))
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  const range = [dayjs().subtract(30, 'day').format('YYYY-MM-DD HH:mm:ss'), dayjs().format('YYYY-MM-DD HH:mm:ss')]
  expect(wrapper.getComponent({ name: 'ARangePicker' }).props('value')).toEqual(range)
  expect(api.listTasks).toHaveBeenCalledTimes(1)
  expect(api.listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, keyword: undefined, instance_id: undefined, engine: undefined, kind: undefined, status: undefined, started_from: dayjs(range[0]).toISOString(), started_to: dayjs(range[1]).add(1, 'second').toISOString() }))
  expect(api.listInstances).not.toHaveBeenCalled()
})
it.each([
  { index: 0, key: 'engine', value: 'redis' },
  { index: 1, key: 'kind', value: 'sql' },
  { index: 2, key: 'status', value: 'succeeded' },
])('执行历史$key选定和清空立即查询并恢复第一页', async ({ index, key, value }) => {
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  const page = wrapper.vm as unknown as { historyFilters: { page: number } }
  const select = wrapper.get('.history-toolbar').findAllComponents(Select)[index]!
  for (const selected of [value, undefined]) {
    page.historyFilters.page = 3
    const calls = api.listTasks.mock.calls.length
    select.vm.$emit('update:value', selected)
    select.vm.$emit('change', selected)
    await flushPromises()
    expect(api.listTasks).toHaveBeenCalledTimes(calls + 1)
    expect(api.listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, [key]: selected }))
  }
})
it('执行历史只对完整时间范围change立即查询，快捷项与清空均回到第一页', async () => {
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  const page = wrapper.vm as unknown as { historyFilters: { page: number } }
  const picker = wrapper.getComponent({ name: 'ARangePicker' })
  picker.vm.$emit('calendarChange', ['2026-01-01 00:00:01', null])
  await flushPromises()
  expect(api.listTasks).toHaveBeenCalledTimes(1)
  const preset = picker.props('presets')[0].value.map((value: dayjs.Dayjs) => value.format('YYYY-MM-DD HH:mm:ss'))
  for (const range of [['2026-01-01 00:00:01', '2026-01-02 00:00:02'], preset, null]) {
    page.historyFilters.page = 4
    const calls = api.listTasks.mock.calls.length
    picker.vm.$emit('update:value', range)
    picker.vm.$emit('change', range)
    await flushPromises()
    expect(api.listTasks).toHaveBeenCalledTimes(calls + 1)
    expect(api.listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, started_from: range ? dayjs(range[0]).toISOString() : undefined, started_to: range ? dayjs(range[1]).add(1, 'second').toISOString() : undefined }))
  }
})
it('执行历史搜索只回车提交，普通输入不请求且不随其他筛选提交草稿，清空图标立即恢复列表', async () => {
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  const toolbar = wrapper.get('.history-toolbar'), input = toolbar.get('input[aria-label="搜索执行历史"]')
  const page = wrapper.vm as unknown as { historyFilters: { page: number } }
  await input.setValue('  SELECT %_  ')
  await input.setValue('')
  expect(api.listTasks).toHaveBeenCalledTimes(1)
  await input.setValue('  SELECT %_  ')
  page.historyFilters.page = 3
  await input.trigger('keydown', { key: 'Enter', keyCode: 13 })
  await flushPromises()
  expect(api.listTasks).toHaveBeenCalledTimes(2)
  expect(api.listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, keyword: 'SELECT %_' }))
  await input.setValue('未提交草稿')
  const engine = toolbar.findAllComponents(Select)[0]!
  engine.vm.$emit('update:value', 'mysql')
  engine.vm.$emit('change', 'mysql')
  await flushPromises()
  expect(api.listTasks).toHaveBeenCalledTimes(3)
  expect(api.listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ keyword: 'SELECT %_', engine: 'mysql' }))
  page.historyFilters.page = 4
  await toolbar.get('.ant-input-clear-icon').trigger('click')
  await flushPromises()
  expect(api.listTasks).toHaveBeenCalledTimes(4)
  expect(api.listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, keyword: undefined, engine: 'mysql' }))
})
it('执行历史与审计筛选独立，审计切回历史重新恢复最近30天且只发一次列表请求', async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-11-09T13:14:15+08:00'))
  const wrapper = mount(Records, { ...options, props: { audit: true } })
  wrappers.push(wrapper)
  await flushPromises()
  const page = wrapper.vm as unknown as { query: { engine: string; keyword: string }; historyFilters: { engine?: string } }
  Object.assign(page.query, { engine: 'redis', keyword: '审计关键词' })
  await wrapper.setProps({ audit: false })
  await flushPromises()
  expect(api.listTasks).toHaveBeenCalledTimes(1)
  expect(api.listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ engine: undefined, keyword: undefined }))
  const input = wrapper.get('input[aria-label="搜索执行历史"]')
  await input.setValue('历史关键词')
  await input.trigger('keydown', { key: 'Enter', keyCode: 13 })
  const engine = wrapper.get('.history-toolbar').findAllComponents(Select)[0]!
  engine.vm.$emit('update:value', 'mysql')
  engine.vm.$emit('change', 'mysql')
  await flushPromises()
  await wrapper.setProps({ audit: true })
  await flushPromises()
  expect(api.listAudit).toHaveBeenLastCalledWith(expect.objectContaining({ engine: 'redis', keyword: '审计关键词' }))
  vi.setSystemTime(new Date('2026-11-10T14:15:16+08:00'))
  const calls = api.listTasks.mock.calls.length
  await wrapper.setProps({ audit: false })
  await flushPromises()
  const range = [dayjs().subtract(30, 'day').format('YYYY-MM-DD HH:mm:ss'), dayjs().format('YYYY-MM-DD HH:mm:ss')]
  expect(api.listTasks).toHaveBeenCalledTimes(calls + 1)
  expect(api.listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, engine: undefined, kind: undefined, status: undefined, keyword: undefined, started_from: dayjs(range[0]).toISOString(), started_to: dayjs(range[1]).add(1, 'second').toISOString() }))
  expect(wrapper.getComponent({ name: 'ARangePicker' }).props('value')).toEqual(range)
  expect((wrapper.get('input[aria-label="搜索执行历史"]').element as HTMLInputElement).value).toBe('')
})
it('执行历史连续自动筛选保留请求代次保护，迟到列表不能覆盖新筛选结果', async () => {
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  let old!: (value: { items: typeof task[]; total: number }) => void
  api.listTasks.mockImplementationOnce(() => new Promise(resolve => { old = resolve }))
    .mockResolvedValueOnce({ items: [{ ...task, instance_name: '新筛选结果' }], total: 1 })
  const engine = wrapper.get('.history-toolbar').findAllComponents(Select)[0]!
  for (const value of ['redis', 'mysql']) {
    engine.vm.$emit('update:value', value)
    engine.vm.$emit('change', value)
    await flushPromises()
  }
  old({ items: [{ ...task, instance_name: '旧筛选结果' }], total: 1 })
  await flushPromises()
  expect(wrapper.text()).toContain('新筛选结果')
  expect(wrapper.text()).not.toContain('旧筛选结果')
})
it('执行历史重设计：结果到期清空表格但保留操作详情', async () => {
  vi.useFakeTimers()
  api.getTask.mockResolvedValue({ ...task, status: 'succeeded' })
  api.getResult.mockResolvedValue({ ...result, expires_at: new Date(Date.now() + 2000).toISOString() })
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  const page = wrapper.vm as unknown as { choose: (id: number) => Promise<void>; viewResult: () => Promise<void> }
  await page.choose(31)
  await page.viewResult()
  await flushPromises()
  expect(wrapper.findComponent(ResultTable).exists()).toBe(true)
  await vi.advanceTimersByTimeAsync(2100)
  await flushPromises()
  expect(wrapper.findComponent(ResultTable).exists()).toBe(false)
  expect(wrapper.text()).not.toContain('9007199254740993')
  expect(wrapper.text()).toContain('结果已过期')
  expect(wrapper.text()).toContain('操作详情 · #31')
})
it('执行历史结果入口：停留结果页直到任务完成后可显式读取，不自动拉取', async () => {
  vi.useFakeTimers()
  api.getTask.mockResolvedValueOnce({ ...task, status: 'running' }).mockResolvedValue({ ...task, status: 'succeeded' })
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await (wrapper.vm as unknown as { choose: (id: number) => Promise<void> }).choose(31)
  await flushPromises()
  await wrapper.findAll('.ant-tabs-tab').find(tab => tab.text() === '结果')!.trigger('click')
  await vi.advanceTimersByTimeAsync(1500)
  await flushPromises()
  expect(api.getResult).not.toHaveBeenCalled()
  const button = wrapper.get('.ant-tabs-tabpane-active').findAll('button').find(item => item.text() === '查看结果')
  expect(button).toBeDefined()
  await button!.trigger('click')
  await flushPromises()
  expect(wrapper.findComponent(ResultTable).exists()).toBe(true)
  expect(api.getResult).toHaveBeenCalledTimes(1)
})
it('执行历史重设计：离开路由中止详情请求并禁止旧响应回写', async () => {
  let finish!: (value: typeof task) => void
  state.route.path = '/database/history'
  state.route.query = { task_id: '31' }
  api.getTask.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  const signal = api.getTask.mock.calls[0]![1] as AbortSignal
  state.route.path = '/database/workbench'
  await flushPromises()
  expect(signal.aborted).toBe(true)
  finish({ ...task, submitted_text: '离开后不可显示的提交文本' })
  await flushPromises()
  expect(wrapper.findComponent({ name: 'ADrawer' }).props('open')).toBe(false)
  expect(wrapper.text()).not.toContain('离开后不可显示')
})
it('执行历史重设计：仅维护权限不出现工作台或结果入口，也不能直接读取结果', async () => {
  state.permissions = new Set(['database:write'])
  api.getTask.mockResolvedValue({ ...task, status: 'succeeded' })
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  const page = wrapper.vm as unknown as { choose: (id: number) => Promise<void>; viewResult: () => Promise<void> }
  await page.choose(31)
  await page.viewResult()
  await flushPromises()
  expect(wrapper.findAll('button').some(button => /工作台|查看结果/.test(button.text()))).toBe(false)
  expect(wrapper.findAll('.ant-tabs-tab').some(tab => tab.text() === '结果')).toBe(false)
  expect(api.getResult).not.toHaveBeenCalled()
})
it('执行历史结果入口：连接测试只显示安全摘要，不提供结果页签', async () => {
  api.getTask.mockResolvedValue({ ...task, kind: 'probe', status: 'succeeded', result_summary: { message: '连接与只读检查通过', read_only_checks: { internal_fact: '不应直接展示的原始对象' } } })
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  const page = wrapper.vm as unknown as { choose: (id: number) => Promise<void>; viewResult: () => Promise<void> }
  await page.choose(31)
  await page.viewResult()
  await flushPromises()
  expect(wrapper.text()).toContain('连接与只读检查通过')
  expect(wrapper.text()).not.toContain('不应直接展示的原始对象')
  expect(wrapper.findAll('.ant-tabs-tab').some(tab => tab.text() === '结果')).toBe(false)
  expect(api.getResult).not.toHaveBeenCalled()
})
it('执行历史重设计：带入工作台只传任务编号，不自动提交查询', async () => {
  api.getTask.mockResolvedValue({ ...task, status: 'succeeded' })
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await (wrapper.vm as unknown as { choose: (id: number) => Promise<void> }).choose(31)
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text().includes('带入工作台编辑'))!.trigger('click')
  expect(navigation.push).toHaveBeenCalledWith({ path: '/database/workbench', query: { task_id: 31 } })
  expect(api.submitTask).not.toHaveBeenCalled()
})
it('执行历史权限失效：撤销查询权限后立即清空旧列表，按维护权限重载', async () => {
  state.permissions = reactive(new Set(['database:query', 'database:write']))
  api.listTasks.mockResolvedValueOnce({ items: [{ ...task, operation_summary: '旧 SQL 历史敏感摘要' }], total: 1 })
  let finish!: (value: { items: typeof task[]; total: number }) => void
  let old!: (value: { items: typeof task[]; total: number }) => void
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  expect(wrapper.text()).toContain('旧 SQL 历史敏感摘要')
  api.listTasks.mockImplementationOnce(() => new Promise(resolve => { old = resolve }))
    .mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const pending = (wrapper.vm as unknown as { load: () => Promise<void> }).load()
  state.permissions.delete('database:query')
  await flushPromises()
  expect(wrapper.text()).not.toContain('旧 SQL 历史敏感摘要')
  expect(api.listTasks).toHaveBeenCalledTimes(3)
  finish({ items: [{ ...task, kind: 'probe', operation_summary: '当前可见的连接测试' }], total: 1 })
  await flushPromises()
  expect(wrapper.text()).toContain('当前可见的连接测试')
  old({ items: [{ ...task, operation_summary: '迟到的旧权限 SQL 摘要' }], total: 1 })
  await pending
  await flushPromises()
  expect(wrapper.text()).not.toContain('迟到的旧权限 SQL 摘要')
  expect(wrapper.text()).toContain('当前可见的连接测试')
})
it.each([40101, 40301, 40302])('执行历史权限失效：列表拒绝%s时清屏并废止在途详情', async code => {
  api.listTasks.mockResolvedValueOnce({ items: [{ ...task, operation_summary: '权限失效前的敏感摘要' }], total: 1 })
  let finish!: (value: typeof task) => void
  api.getTask.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  const page = wrapper.vm as unknown as { choose: (id: number) => Promise<void>; load: () => Promise<void>; total: number }
  const pending = page.choose(31)
  await flushPromises()
  const signal = api.getTask.mock.calls[0]![1] as AbortSignal
  api.listTasks.mockRejectedValueOnce(new ApiError(code, '历史读取权限失效'))
  await page.load()
  await flushPromises()
  expect(wrapper.text()).not.toContain('权限失效前的敏感摘要')
  expect(page.total).toBe(0)
  expect(signal.aborted).toBe(true)
  expect(wrapper.findComponent({ name: 'ADrawer' }).props('open')).toBe(false)
  finish({ ...task, submitted_text: '权限失效后不可回写的 SQL' })
  await pending
  await flushPromises()
  expect(wrapper.text()).not.toContain('权限失效后不可回写')
  expect(wrapper.text()).toContain('历史读取权限失效')
})
it.each([false, true])('执行历史权限失效：审计列表被拒绝时清除审计详情（在途=%s）', async pendingDetail => {
  const audit = { ...task, id: 31, actor_id: 8, actor_name: '不可继续展示的审计对象', source_ip: '192.0.2.1', events: [] }
  api.listAudit.mockResolvedValueOnce({ items: [audit], total: 1 })
  api.getAudit.mockResolvedValueOnce(audit)
  const wrapper = mount(Records, { ...options, props: { audit: true } })
  wrappers.push(wrapper)
  await flushPromises()
  const page = wrapper.vm as unknown as { choose: (id: number) => Promise<void>; load: () => Promise<void>; total: number }
  await page.choose(31)
  await flushPromises()
  expect(wrapper.text()).toContain('不可继续展示的审计对象')
  let finish!: (value: typeof audit) => void
  if (pendingDetail) api.getAudit.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const pending = pendingDetail ? page.choose(32) : Promise.resolve()
  api.listAudit.mockRejectedValueOnce(new ApiError(40301, '审计权限已撤销'))
  await page.load()
  if (pendingDetail) finish({ ...audit, task_id: 32 })
  await pending
  await flushPromises()
  expect(wrapper.text()).not.toContain('不可继续展示的审计对象')
  expect(wrapper.text()).not.toContain('审计详情 ·')
  expect(page.total).toBe(0)
  expect(wrapper.text()).toContain('审计权限已撤销')
})
it('本人历史切到审计时重新取审计列表并移除本人任务状态', async () => {
  api.listTasks.mockResolvedValue({ items: [task], total: 1 })
  const wrapper = mount(Records, { ...options, props: { audit: false } })
  wrappers.push(wrapper)
  await flushPromises()
  await (wrapper.vm as unknown as {
    choose: (id: number) => Promise<void>
  }).choose(31)
  await flushPromises()
  expect(wrapper.text()).toContain('操作详情 · #31')
  await wrapper.setProps({ audit: true })
  await flushPromises()
  expect(api.listAudit).toHaveBeenCalledTimes(1)
  expect(wrapper.text()).not.toContain('操作详情 · #31')
})
const databaseConfigs = {
  'database.execution_timeout_seconds': 60, 'database.max_result_rows': 1000,
  'database.max_result_bytes': 5242880, 'database.result_ttl_minutes': 30,
  'database.history_retention_days': 30, 'database.audit_retention_days': 90,
}
function mountDatabaseSettings(configs: Record<string, unknown> | null = databaseConfigs) {
  const wrapper = mount(DatabaseSettingsPanel, {
    props: { configs },
    global: { plugins: [Antd] }
  })
  wrappers.push(wrapper)
  return wrapper
}
it('数据库策略仅头部一个保存入口，一次提交改动键且点击不会折叠', async () => {
  const wrapper = mountDatabaseSettings()
  await flushPromises()
  const saves = wrapper.findAll('button').filter(button => button.text().replace(/\s/g, '') === '保存')
  expect(saves).toHaveLength(1)
  expect(wrapper.get('.db-settings-head').find('button.ant-btn-primary').exists()).toBe(true)
  expect(saves[0]!.attributes('disabled')).toBeUndefined()
  await wrapper.get('#db-setting-execution_timeout_seconds').setValue('45')
  await wrapper.get('#db-setting-max_result_rows').setValue('500')
  await saves[0]!.trigger('click')
  await flushPromises()
  expect(sys.updateConfigs).toHaveBeenCalledTimes(1)
  expect(sys.updateConfigs).toHaveBeenCalledWith({ 'database.execution_timeout_seconds': 45, 'database.max_result_rows': 500 })
  expect(wrapper.get('.db-settings-head').classes()).not.toContain('is-collapsed')
  expect(saves[0]!.attributes('disabled')).toBeUndefined()
})
it.each([
  ['execution_timeout_seconds', '2.5'], ['max_result_rows', '10001'],
  ['max_result_bytes', '65535'], ['result_ttl_minutes', '0'],
  ['history_retention_days', '366'], ['audit_retention_days', '1096'],
])('数据库策略整体校验%s=%s，任一无效不提交其他有效改动', async (key, invalid) => {
  const wrapper = mountDatabaseSettings()
  await wrapper.get('#db-setting-max_result_rows').setValue('500')
  await wrapper.get(`#db-setting-${key}`).setValue(invalid)
  await wrapper.get('.db-settings-head button.ant-btn-primary').trigger('click')
  await flushPromises()
  expect(sys.updateConfigs).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('整数')
})
it.each(['history_retention_days', 'audit_retention_days'])('数据库策略缩短%s统一确认，取消后可重试且整批一次提交', async key => {
  const confirm = vi.spyOn(Modal, 'confirm').mockReturnValue({ destroy: vi.fn(), update: vi.fn() })
  const wrapper = mountDatabaseSettings()
  await wrapper.get(`#db-setting-${key}`).setValue('15')
  await wrapper.get('#db-setting-max_result_rows').setValue('500')
  const save = wrapper.get('.db-settings-head button.ant-btn-primary')
  await save.trigger('click')
  await save.trigger('click')
  expect(confirm).toHaveBeenCalledTimes(1)
  expect(sys.updateConfigs).not.toHaveBeenCalled()
  await confirm.mock.calls[0]![0].onCancel!()
  await flushPromises()
  expect((wrapper.get(`#db-setting-${key}`).element as HTMLInputElement).value).toBe('15')
  await save.trigger('click')
  await confirm.mock.calls[1]![0].onOk!()
  await flushPromises()
  expect(sys.updateConfigs).toHaveBeenCalledTimes(1)
  expect(sys.updateConfigs).toHaveBeenCalledWith({ [`database.${key}`]: 15, 'database.max_result_rows': 500 })
  expect(save.attributes('disabled')).toBeUndefined()
})
it('数据库策略保存失败保持整批输入与saved基线，重试请求去重且成功后才更新', async () => {
  let fail!: (error: Error) => void
  sys.updateConfigs.mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject }))
  const wrapper = mountDatabaseSettings()
  await wrapper.get('#db-setting-execution_timeout_seconds').setValue('45')
  await wrapper.get('#db-setting-audit_retention_days').setValue('120')
  const save = wrapper.get('.db-settings-head button.ant-btn-primary')
  await save.trigger('click')
  await save.trigger('click')
  expect(sys.updateConfigs).toHaveBeenCalledTimes(1)
  expect(save.classes()).toContain('ant-btn-loading')
  expect(wrapper.get('#db-setting-execution_timeout_seconds').attributes('disabled')).toBeDefined()
  fail(new Error('批量保存失败'))
  await flushPromises()
  expect(wrapper.text()).toContain('批量保存失败')
  expect((wrapper.get('#db-setting-execution_timeout_seconds').element as HTMLInputElement).value).toBe('45')
  expect(save.attributes('disabled')).toBeUndefined()
  await save.trigger('click')
  await flushPromises()
  expect(sys.updateConfigs).toHaveBeenCalledTimes(2)
  expect(sys.updateConfigs).toHaveBeenLastCalledWith({ 'database.execution_timeout_seconds': 45, 'database.audit_retention_days': 120 })
  expect(save.attributes('disabled')).toBeUndefined()
})
it('数据库策略同时缩短历史与审计只确认一次，结果TTL缩短按快照直接保存', async () => {
  const confirm = vi.spyOn(Modal, 'confirm').mockReturnValue({ destroy: vi.fn(), update: vi.fn() })
  const wrapper = mountDatabaseSettings()
  await wrapper.get('#db-setting-history_retention_days').setValue('15')
  await wrapper.get('#db-setting-audit_retention_days').setValue('30')
  const save = wrapper.get('.db-settings-head button.ant-btn-primary')
  await save.trigger('click')
  expect(confirm).toHaveBeenCalledTimes(1)
  await confirm.mock.calls[0]![0].onOk!()
  await flushPromises()
  expect(sys.updateConfigs).toHaveBeenCalledWith({ 'database.history_retention_days': 15, 'database.audit_retention_days': 30 })
  await wrapper.get('#db-setting-result_ttl_minutes').setValue('15')
  await save.trigger('click')
  await flushPromises()
  expect(confirm).toHaveBeenCalledTimes(1)
  expect(sys.updateConfigs).toHaveBeenLastCalledWith({ 'database.result_ttl_minutes': 15 })
})
it('数据库策略未加载时保存禁用，不能提交前端默认值', async () => {
  const wrapper = mountDatabaseSettings(null)
  await flushPromises()
  expect(wrapper.get('.db-settings-head button.ant-btn-primary').attributes('disabled')).toBeDefined()
  expect(wrapper.findAll('input').every(input => input.attributes('disabled') !== undefined)).toBe(true)
  expect(sys.updateConfigs).not.toHaveBeenCalled()
})
it('数据库策略无改动也保存服务端当前六项，重复点击去重并反馈成功', async () => {
  let complete!: () => void
  sys.updateConfigs.mockImplementationOnce(() => new Promise<void>(resolve => { complete = resolve }))
  const success = vi.spyOn(message, 'success')
  const wrapper = mountDatabaseSettings()
  const save = wrapper.get('.db-settings-head button.ant-btn-primary')
  expect(save.attributes('disabled')).toBeUndefined()
  await save.trigger('click')
  await save.trigger('click')
  expect(sys.updateConfigs).toHaveBeenCalledTimes(1)
  expect(sys.updateConfigs).toHaveBeenCalledWith(databaseConfigs)
  expect(success).not.toHaveBeenCalled()
  complete()
  await flushPromises()
  expect(success).toHaveBeenCalledWith('数据库配置已保存')
  expect(save.attributes('disabled')).toBeUndefined()
})
it('数据库策略无改动保存失败显示错误且允许重试', async () => {
  sys.updateConfigs.mockRejectedValueOnce(new Error('无改动保存失败'))
  const wrapper = mountDatabaseSettings()
  const save = wrapper.get('.db-settings-head button.ant-btn-primary')
  await save.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('无改动保存失败')
  expect(save.attributes('disabled')).toBeUndefined()
  await save.trigger('click')
  await flushPromises()
  expect(sys.updateConfigs).toHaveBeenCalledTimes(2)
  expect(sys.updateConfigs).toHaveBeenLastCalledWith(databaseConfigs)
})
it('数据库策略配置缺项时不补前端默认值也不提交部分配置', async () => {
  const wrapper = mountDatabaseSettings({ 'database.execution_timeout_seconds': 120 })
  await wrapper.get('.db-settings-head button.ant-btn-primary').trigger('click')
  await flushPromises()
  expect(wrapper.get('#db-setting-max_result_rows').element).toHaveProperty('value', '')
  expect(wrapper.text()).toContain('整数')
  expect(sys.updateConfigs).not.toHaveBeenCalled()
})
