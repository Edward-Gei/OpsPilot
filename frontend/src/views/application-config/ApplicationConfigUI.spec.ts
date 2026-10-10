// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { config, flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import { createPinia } from 'pinia'
import { reactive } from 'vue'
import ConfigContentEditor from './ConfigContentEditor.vue'
import DriftCompareDrawer from './DriftCompareDrawer.vue'
import ApplicationConfigList from './ApplicationConfigList.vue'
import NewConfigDrawer from './NewConfigDrawer.vue'
import ApplicationConfigDetail from './ApplicationConfigDetail.vue'
import ConfigVersionCompare from './ConfigVersionCompare.vue'
import TodoList from '@/views/ticket/TodoList.vue'

const api = vi.hoisted(() => ({
  getConfigFile: vi.fn(), getDriftView: vi.fn(), listVersions: vi.fn(),
  importDrift: vi.fn(), publishVersion: vi.fn(), getConfigTask: vi.fn(),
  listConfigFiles: vi.fn(), listConfigImportTasks: vi.fn(), syncConfigFile: vi.fn(),
  listPlatformInstances: vi.fn(), listApprovalRoles: vi.fn(), listCmdbApplications: vi.fn(),
  listNacosNamespaces: vi.fn(), discoverConfigResources: vi.fn(), createConfigFile: vi.fn(),
  getDraft: vi.fn(), saveDraft: vi.fn(), discardDraft: vi.fn(), submitCandidate: vi.fn(),
  listConfigFileTasks: vi.fn(), getVersionContent: vi.fn(), getVersionComparison: vi.fn(),
  listConfigApprovalTodo: vi.fn(), getConfigApprovalContent: vi.fn(), readSecret: false,
}))
vi.mock('@/api/applicationConfig', () => api)
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: (perm: string) => perm === 'config:write' || (perm === 'secret:read' && api.readSecret) }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }), useRoute: () => detailRoute }))
const detailRoute = reactive({ name: 'application-config-edit', params: { id: '2' } })

const file = (id: number) => ({
  id, name: `test-${id}.yaml`, description: null, platform_instance_id: 1,
  platform_instance_name: 'nacos', provider: 'nacos', locator: { group: 'DEFAULT_GROUP', data_id: `test-${id}.yaml` },
  content_format: 'yaml', approval_role_id: 1, approval_role_name: '管理员', application_ids: [], application_count: 0,
  current_version_id: 6, current_version_no: 6, current_snapshot_id: 1, latest_snapshot_id: 1,
  status: 'active', drift_status: 'drifted', last_synced_at: null, last_sync_error: null, created_at: null, updated_at: null,
})
const drift = { drift_status: 'drifted', latest_snapshot_id: 1, baseline_version_id: 6,
  baseline_content: 'key: internal', external_exists: true, external_content: 'key: external' }
const task = (id: number, fileId: number, status: string) => ({
  id, kind: 'sync', config_file_id: fileId, config_version_id: null, status, total_count: 1,
  success_count: 0, skipped_count: 0, failed_count: 0, last_error: null, created_at: null, finished_at: null,
})

config.global.plugins = [Antd, createPinia()]
class TestResizeObserver { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal('ResizeObserver', TestResizeObserver)
const getComputedStyle = window.getComputedStyle.bind(window)
window.getComputedStyle = ((element: Element) => getComputedStyle(element)) as typeof window.getComputedStyle
Range.prototype.getClientRects = () => [new DOMRect(0, 0, 0, 0)] as unknown as DOMRectList
Range.prototype.getBoundingClientRect = () => new DOMRect(0, 0, 0, 0)
window.matchMedia = vi.fn().mockImplementation((media: string) => ({
  matches: false, media, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
  addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  detailRoute.name = 'application-config-edit'
  detailRoute.params.id = '2'
  api.readSecret = false
  api.getConfigFile.mockResolvedValue(file(2))
  api.getDriftView.mockResolvedValue(drift)
  api.listVersions.mockResolvedValue({ items: [{ id: 6, version_no: 6, status: 'published' }] })
  api.listConfigFiles.mockResolvedValue({ items: [file(1), file(2)], total: 2 })
  api.listConfigImportTasks.mockResolvedValue({ items: [] })
  api.listPlatformInstances.mockResolvedValue({ items: [
    { id: 1, name: 'nacos-a', provider: 'nacos', enabled: true },
    { id: 2, name: 'nacos-b', provider: 'nacos', enabled: true },
  ] })
  api.listApprovalRoles.mockResolvedValue({ items: [{ id: 3, name: '审批角色' }] })
  api.listCmdbApplications.mockResolvedValue({ items: [] })
  api.listConfigFileTasks.mockResolvedValue({ items: [] })
  api.getVersionContent.mockResolvedValue({ content: '# 正式版本\nkey: published\n' })
  api.getVersionComparison.mockResolvedValue({ version_id: 6, version_no: 6, content_format: 'yaml',
    content: '# 新注释\nkey: updated\n', base_version_id: 4, base_version_no: 4,
    base_content: '# 旧注释\nkey: old\n', has_changes: true })
  api.listConfigApprovalTodo.mockResolvedValue({ items: [{ version_id: 6, file_id: 2, file_name: 'test-2.yaml',
    version_no: 6, approval_role_name: '审批角色', submitter_name: '提交人', submitted_at: '2026-10-09T10:00:00' }], total: 1 })
  api.getConfigApprovalContent.mockImplementation(() => api.getVersionComparison())
  api.getDraft.mockResolvedValue({ id: null, content: '# 正式版本\nkey: published\n', base_version_id: 6, base_snapshot_id: 1 })
  api.saveDraft.mockResolvedValue(null)
  api.discardDraft.mockResolvedValue(null)
  api.submitCandidate.mockResolvedValue({ id: 7 })
  api.listNacosNamespaces.mockResolvedValue({ items: [
    { id: '', name: 'public' }, { id: 'stage-id', name: 'stage' },
  ] })
  api.discoverConfigResources.mockResolvedValue({ items: [
    { locator: { namespace: 'stage-id', group: 'PAYMENT', data_id: 'a.yaml' }, display_name: 'a.yaml', revision: null },
    { locator: { namespace: 'stage-id', group: 'PAYMENT', data_id: 'b.yaml' }, display_name: 'b.yaml', revision: null },
    { locator: { namespace: 'stage-id', group: 'ORDERS', data_id: 'c.yaml' }, display_name: 'c.yaml', revision: null },
  ] })
})
afterEach(() => { document.body.innerHTML = '' })

describe('编辑页草稿恢复', () => {
  function mountDetail() {
    return mount(ApplicationConfigDetail, { attachTo: document.body,
      global: { stubs: { DriftCompareDrawer: true } } })
  }
  async function clickButton(wrapper: ReturnType<typeof mountDetail>, text: string) {
    await wrapper.findAll('button').find(button => button.text().includes(text))!.trigger('click')
    await flushPromises()
  }

  it('重试发布从请求发起到任务结束保持加载，失败后可再次点击', async () => {
    api.getConfigFile.mockResolvedValue({ ...file(2), drift_status: 'clean' })
    api.listVersions.mockResolvedValue({ items: [{ id: 6, version_no: 6, status: 'approved' }] })
    api.listConfigFileTasks.mockResolvedValue({ items: [{ ...task(11, 2, 'failed'), kind: 'publish', config_version_id: 6 }] })
    let resolvePublish!: (value: unknown) => void
    let resolveTask!: (value: unknown) => void
    api.publishVersion.mockImplementation(() => new Promise((resolve) => { resolvePublish = resolve }))
    api.getConfigTask.mockImplementation(() => new Promise((resolve) => { resolveTask = resolve }))
    const wrapper = mountDetail()
    await flushPromises()
    await wrapper.find('[role="tab"][id$="-versions"]').trigger('click')
    const retry = () => wrapper.findAll('button').find(button => button.text().includes('重试发布'))!
    await retry().trigger('click')
    expect(retry().classes()).toContain('ant-btn-loading')
    expect(retry().attributes('disabled')).toBeDefined()
    resolvePublish({ ...task(12, 2, 'queued'), kind: 'publish', config_version_id: 6 })
    await flushPromises()
    expect(retry().classes()).toContain('ant-btn-loading')
    api.listVersions.mockResolvedValue({ items: [{ id: 6, version_no: 6, status: 'publishing' }] })
    await (wrapper.vm as unknown as { load: () => Promise<void> }).load()
    await flushPromises()
    expect(retry()).toBeDefined()
    expect(retry().classes()).toContain('ant-btn-loading')
    api.listVersions.mockResolvedValue({ items: [{ id: 6, version_no: 6, status: 'approved' }] })
    resolveTask({ ...task(12, 2, 'failed'), kind: 'publish', config_version_id: 6, last_error: '远端拒绝' })
    await flushPromises()
    expect(retry().classes()).not.toContain('ant-btn-loading')
    expect(retry().attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('版本记录展示名称、三项时间、审批人和失败原因', async () => {
    api.getConfigFile.mockResolvedValue({ ...file(2), last_synced_at: '2026-10-10T16:03:00' })
    api.listConfigFiles.mockResolvedValue({ items: [{ ...file(2), last_synced_at: '2026-10-10T16:03:00' }], total: 1 })
    api.listVersions.mockResolvedValue({ items: [{ id: 6, version_no: 6, status: 'approved', source: 'opspilot_publish',
      approval_role_id: 1, submitted_by: 1, approval_role_name: '生产审批', submitter_name: '张三', approver_name: '李四',
      submitted_at: '2026-10-10T13:00:00', approved_at: '2026-10-10T14:00:00', completed_at: '2026-10-10T15:02:00',
      failure_reason: '远端拒绝' }] })
    const wrapper = mountDetail()
    await flushPromises()
    await wrapper.find('[role="tab"][id$="-versions"]').trigger('click')
    const table = wrapper.find('.ant-table')
    for (const title of ['审批角色', '提交人', '提交时间', '审批时间', '完成时间', '审批人', '失败原因']) expect(table.text()).toContain(title)
    for (const text of ['生产审批', '张三', '李四', '远端拒绝']) expect(table.text()).toContain(text)
    expect(table.text()).not.toContain('角色 ID')
    for (const time of ['13:00:00', '14:00:00', '15:02:00']) expect(table.text()).toContain(time)
    expect(wrapper.text()).toContain('16:03:00')
    expect(wrapper.text()).not.toMatch(/\b(?:AM|PM)\b/)
    wrapper.unmount()
    const list = mount(ApplicationConfigList)
    await flushPromises()
    expect(list.find('.ant-table').text()).toContain('16:03:00')
    expect(list.find('.ant-table').text()).not.toMatch(/\b(?:AM|PM)\b/)
    list.unmount()
  })

  it('版本记录查看显示旧正式版本和所选版本的只读差异', async () => {
    const wrapper = mountDetail()
    await flushPromises()
    await wrapper.find('[role="tab"][id$="-versions"]').trigger('click')
    await wrapper.find('.ant-table button').trigger('click')
    await flushPromises()
    expect(api.getVersionComparison).toHaveBeenCalledWith(2, 6)
    const dialog = document.querySelector('.ant-modal-body')!
    expect(dialog.textContent).toContain('旧正式版本 v4')
    expect(dialog.textContent).toContain('当前查看版本 v6')
    expect(dialog.textContent).toContain('# 旧注释')
    expect(dialog.textContent).toContain('# 新注释')
    expect(dialog.querySelectorAll('.cm-mergeView').length).toBe(1)
    expect(dialog.querySelector('[contenteditable="true"]')).toBeNull()
    wrapper.unmount()
  })

  it('重新进入编辑页自动展示已保存草稿和未发布提示', async () => {
    api.getDraft.mockResolvedValue({ id: 9, content: '# 保存的注释\nkey: draft\n', base_version_id: 6, base_snapshot_id: 1 })
    const wrapper = mountDetail()
    await flushPromises()
    expect(wrapper.findComponent(ConfigContentEditor).props('modelValue')).toBe('# 保存的注释\nkey: draft\n')
    expect(wrapper.text()).toContain('已保存草稿')
    expect(wrapper.text()).toContain('尚未发布')
    wrapper.unmount()
  })

  it('保存后持续提示未发布，再次进入仍显示保存正文', async () => {
    const wrapper = mountDetail()
    await flushPromises()
    await clickButton(wrapper, '编辑草稿')
    wrapper.findComponent(ConfigContentEditor).vm.$emit('update:modelValue', '# 新注释\nkey: modified\n')
    await flushPromises()
    expect(wrapper.text()).toContain('尚未保存')
    await clickButton(wrapper, '保存草稿')
    expect(wrapper.text()).toContain('已保存草稿')
    expect(wrapper.text()).not.toContain('尚未保存')
    expect(wrapper.findComponent(ConfigContentEditor).props('modelValue')).toBe('# 新注释\nkey: modified\n')
    api.getDraft.mockResolvedValue({ id: 9, content: '# 新注释\nkey: modified\n', base_version_id: 6, base_snapshot_id: 1 })
    wrapper.unmount()
    const reopened = mountDetail()
    await flushPromises()
    expect(reopened.findComponent(ConfigContentEditor).props('modelValue')).toBe('# 新注释\nkey: modified\n')
    reopened.unmount()
  })

  it('没有保存草稿时展示正式版本，手动打开草稿不显示已保存提示', async () => {
    const wrapper = mountDetail()
    await flushPromises()
    expect(wrapper.findComponent(ConfigContentEditor).props('readonly')).toBe(true)
    expect(wrapper.text()).not.toContain('已保存草稿')
    await clickButton(wrapper, '编辑草稿')
    expect(wrapper.text()).not.toContain('已保存草稿')
    wrapper.unmount()
  })

  it('丢弃已保存草稿后恢复正式内容并清除提示', async () => {
    api.getDraft.mockResolvedValue({ id: 9, content: 'key: draft', base_version_id: 6, base_snapshot_id: 1 })
    const wrapper = mountDetail()
    await flushPromises()
    wrapper.findAllComponents({ name: 'APopconfirm' }).find(item => item.props('title') === '确认丢弃草稿？')!.vm.$emit('confirm')
    await flushPromises()
    expect(wrapper.findComponent(ConfigContentEditor).props('modelValue')).toBe('# 正式版本\nkey: published\n')
    expect(wrapper.text()).not.toContain('已保存草稿')
    wrapper.unmount()
  })

  it('TEXT 无敏感信息读取权限时恢复草稿状态但不回填正文', async () => {
    api.getConfigFile.mockResolvedValue({ ...file(2), content_format: 'text' })
    api.getDraft.mockResolvedValue({ id: 9, content: '******', base_version_id: 6, base_snapshot_id: 1 })
    const wrapper = mountDetail()
    await flushPromises()
    expect(wrapper.findComponent(ConfigContentEditor).props('modelValue')).toBe('')
    expect(wrapper.text()).toContain('已保存草稿')
    wrapper.unmount()
  })

  it('只读查看页仍展示正式版本，不提供草稿编辑操作', async () => {
    detailRoute.name = 'application-config-detail'
    api.getDraft.mockResolvedValue({ id: 9, content: 'key: draft', base_version_id: 6, base_snapshot_id: 1 })
    const wrapper = mountDetail()
    await flushPromises()
    expect(wrapper.findComponent(ConfigContentEditor).props('modelValue')).toBe('# 正式版本\nkey: published\n')
    expect(wrapper.findComponent(ConfigContentEditor).props('readonly')).toBe(true)
    expect(wrapper.text()).not.toContain('保存草稿')
    wrapper.unmount()
  })

  it('从只读页切换到编辑页恢复草稿，切回只读显示正式版本', async () => {
    detailRoute.name = 'application-config-detail'
    api.getDraft.mockResolvedValue({ id: 9, content: 'key: draft', base_version_id: 6, base_snapshot_id: 1 })
    const wrapper = mountDetail()
    await flushPromises()
    detailRoute.name = 'application-config-edit'
    await flushPromises()
    expect(wrapper.findComponent(ConfigContentEditor).props('modelValue')).toBe('key: draft')
    detailRoute.name = 'application-config-detail'
    await flushPromises()
    expect(wrapper.findComponent(ConfigContentEditor).props('modelValue')).toBe('# 正式版本\nkey: published\n')
    wrapper.unmount()
  })

  it('编辑信息刷新页面不会覆盖尚未保存的草稿输入', async () => {
    api.getDraft.mockResolvedValue({ id: 9, content: 'key: draft', base_version_id: 6, base_snapshot_id: 1 })
    const wrapper = mountDetail()
    await flushPromises()
    wrapper.findComponent(ConfigContentEditor).vm.$emit('update:modelValue', 'key: unsaved')
    // 漂移处理完成会刷新元信息，当前编辑缓冲仍应保留。
    wrapper.findComponent({ name: 'DriftCompareDrawer' }).vm.$emit('resolved')
    await flushPromises()
    expect(wrapper.findComponent(ConfigContentEditor).props('modelValue')).toBe('key: unsaved')
    expect(wrapper.text()).toContain('尚未保存')
    wrapper.unmount()
  })

  it('提交审批后清除草稿提示并展示候选版本', async () => {
    api.getDraft.mockResolvedValue({ id: 9, content: 'key: draft', base_version_id: 6, base_snapshot_id: 1 })
    const wrapper = mountDetail()
    await flushPromises()
    api.getDraft.mockResolvedValue({ id: null, content: '# 正式版本\nkey: published\n', base_version_id: 6, base_snapshot_id: 1 })
    api.listVersions.mockResolvedValue({ items: [{ id: 6, version_no: 6, status: 'published' },
      { id: 7, version_no: 7, status: 'pending_approval', source: 'opspilot_publish' }] })
    await clickButton(wrapper, '提交审批')
    expect(wrapper.text()).not.toContain('已保存草稿')
    expect(wrapper.text()).toContain('待审批')
    wrapper.unmount()
  })

  it('保存失败时保留未保存提示，不误报草稿已保存', async () => {
    const errors: unknown[] = []
    const wrapper = mount(ApplicationConfigDetail, { attachTo: document.body,
      global: { stubs: { DriftCompareDrawer: true }, config: { errorHandler: error => errors.push(error) } } })
    await flushPromises()
    await clickButton(wrapper, '编辑草稿')
    wrapper.findComponent(ConfigContentEditor).vm.$emit('update:modelValue', 'key: unsaved')
    api.saveDraft.mockRejectedValueOnce(new Error('save failed'))
    await clickButton(wrapper, '保存草稿')
    expect(errors).toHaveLength(1)
    expect(wrapper.text()).toContain('尚未保存')
    expect(wrapper.text()).not.toContain('已保存草稿')
    wrapper.unmount()
  })

  it('草稿请求返回前切换到只读页，不回填草稿或暴露编辑操作', async () => {
    let resolveDraft!: (draft: unknown) => void
    api.getDraft.mockImplementationOnce(() => new Promise(resolve => { resolveDraft = resolve }))
    const wrapper = mountDetail()
    await flushPromises()
    detailRoute.name = 'application-config-detail'
    await flushPromises()
    resolveDraft({ id: 9, content: 'key: draft', base_version_id: 6, base_snapshot_id: 1 })
    await flushPromises()
    expect(wrapper.findComponent(ConfigContentEditor).props('readonly')).toBe(true)
    expect(wrapper.findComponent(ConfigContentEditor).props('modelValue')).toBe('# 正式版本\nkey: published\n')
    expect(wrapper.text()).not.toContain('保存草稿')
    wrapper.unmount()
  })
})

describe('版本差异与审批预览', () => {
  it('审批详情直接使用授权响应的双方正文，不借用历史读取权限', async () => {
    const wrapper = mount(TodoList, { attachTo: document.body, global: { stubs: { TicketDetailDrawer: true } } })
    await flushPromises()
    await wrapper.find('.ant-table button').trigger('click')
    await flushPromises()
    expect(api.getConfigApprovalContent).toHaveBeenCalledWith(6)
    const drawer = document.querySelector('.ant-drawer-body')!
    expect(drawer.textContent).toContain('旧正式版本 v4')
    expect(drawer.textContent).toContain('# 旧注释')
    expect(drawer.textContent).toContain('# 新注释')
    expect(drawer.querySelector('[contenteditable="true"]')).toBeNull()
    wrapper.unmount()
  })

  it('首版显示空基准，纯敏感差异不能误报无变更', async () => {
    const comparison = { version_id: 1, version_no: 1, content_format: 'yaml' as const, content: 'password: masked\n',
      base_version_id: null, base_version_no: null, base_content: '', has_changes: true }
    const wrapper = mount(ConfigVersionCompare, { props: { comparison } })
    expect(wrapper.text()).toContain('空内容（无旧正式版本）')
    await wrapper.setProps({ comparison: { ...comparison, base_version_id: 2, base_version_no: 2, base_content: comparison.content } })
    expect(wrapper.text()).toContain('当前权限无法查看具体变化')
    expect(wrapper.text()).not.toContain('无变更')
    await wrapper.setProps({ comparison: { ...comparison, base_content: comparison.content, has_changes: false } })
    expect(wrapper.text()).toContain('无变更')
    wrapper.unmount()
  })
})

describe('漂移确认', () => {
  it('注释漂移高亮双方差异，选择导入后按覆盖预览重新比较', async () => {
    api.getDriftView.mockResolvedValue({ ...drift,
      baseline_content: '# test1\nkey: same\n', external_content: '# test3\nkey: same\n' })
    const wrapper = mount(DriftCompareDrawer, { props: { open: true, fileId: 2, canWrite: true }, attachTo: document.body })
    await flushPromises()
    const modal = document.querySelector('.ant-modal-body')!
    expect(modal.querySelector('.cm-merge-a .cm-changedText')?.textContent).toContain('1')
    expect(modal.querySelector('.cm-merge-b .cm-changedText')?.textContent).toContain('3')
    expect(modal.querySelector('[contenteditable="true"]')).toBeNull()
    ;(modal.querySelector('button[aria-label="导入外部内容为新版本"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(modal.querySelectorAll('.cm-changedLine')).toHaveLength(0)
    expect(modal.querySelector('.cm-merge-a .cm-content')?.textContent).toContain('# test3')
    expect(modal.querySelector('.cm-merge-b .cm-content')?.textContent).toContain('# test3')
    expect(api.importDrift).not.toHaveBeenCalled()
    expect(api.publishVersion).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('切换版本重新高亮，重新发布方向只预览所选版本', async () => {
    const wrapper = mount(DriftCompareDrawer, { props: { open: true, fileId: 2, canWrite: true }, attachTo: document.body })
    await flushPromises()
    api.getDriftView.mockResolvedValue({ ...drift, baseline_version_id: 4, baseline_content: 'key: previous\n' })
    wrapper.findComponent({ name: 'ASelect' }).vm.$emit('change', 4)
    await flushPromises()
    const modal = document.querySelector('.ant-modal-body')!
    expect(modal.querySelector('.cm-merge-a .cm-content')?.textContent).toContain('key: previous')
    expect(modal.querySelectorAll('.cm-changedLine').length).toBeGreaterThan(0)
    ;(modal.querySelector('button[aria-label="重新发布 OpsPilot 版本"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(modal.querySelectorAll('.cm-changedLine')).toHaveLength(0)
    expect(modal.querySelector('.cm-merge-b .cm-content')?.textContent).toContain('key: previous')
    expect(api.publishVersion).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('远端缺失时显示说明并高亮本地内容，保留重新发布预览', async () => {
    api.getDriftView.mockResolvedValue({ ...drift, drift_status: 'remote_missing', external_exists: false, external_content: null })
    const wrapper = mount(DriftCompareDrawer, { props: { open: true, fileId: 2, canWrite: true }, attachTo: document.body })
    await flushPromises()
    const modal = document.querySelector('.ant-modal-body')!
    expect(modal.textContent).toContain('远端缺失')
    expect(modal.querySelectorAll('.cm-merge-a .cm-changedLine').length).toBeGreaterThan(0)
    expect((modal.querySelector('button[aria-label="导入外部内容为新版本"]') as HTMLButtonElement).disabled).toBe(true)
    ;(modal.querySelector('button[aria-label="重新发布 OpsPilot 版本"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(modal.querySelector('.cm-merge-b .cm-content')?.textContent).toContain('key: internal')
    wrapper.unmount()
  })

  it('导入成功后关闭弹窗并通知刷新', async () => {
    api.importDrift.mockResolvedValue({})
    const wrapper = mount(DriftCompareDrawer, { props: { open: true, fileId: 2, canWrite: true }, attachTo: document.body })
    await flushPromises()
    ;(document.querySelector('button[aria-label="导入外部内容为新版本"]') as HTMLButtonElement).click()
    await flushPromises()
    ;(document.querySelector('.ant-modal-footer .ant-btn-primary') as HTMLButtonElement).click()
    await flushPromises()
    expect(wrapper.emitted('resolved')).toHaveLength(1)
    expect(wrapper.emitted('update:open')).toEqual([[false]])
    wrapper.unmount()
  })

  it('重新发布失败时保留弹窗以展示错误', async () => {
    api.publishVersion.mockResolvedValue(task(10, 2, 'failed'))
    const wrapper = mount(DriftCompareDrawer, { props: { open: true, fileId: 2, canWrite: true }, attachTo: document.body })
    await flushPromises()
    ;(document.querySelector('button[aria-label="重新发布 OpsPilot 版本"]') as HTMLButtonElement).click()
    await flushPromises()
    ;(document.querySelector('.ant-modal-footer .ant-btn-primary') as HTMLButtonElement).click()
    await flushPromises()
    expect(wrapper.emitted('update:open')).toBeUndefined()
    wrapper.unmount()
  })

  it('重新发布任务成功后关闭弹窗', async () => {
    api.publishVersion.mockResolvedValue(task(10, 2, 'success'))
    const wrapper = mount(DriftCompareDrawer, { props: { open: true, fileId: 2, canWrite: true }, attachTo: document.body })
    await flushPromises()
    ;(document.querySelector('button[aria-label="重新发布 OpsPilot 版本"]') as HTMLButtonElement).click()
    await flushPromises()
    ;(document.querySelector('.ant-modal-footer .ant-btn-primary') as HTMLButtonElement).click()
    await flushPromises()
    expect(wrapper.emitted('resolved')).toHaveLength(1)
    expect(wrapper.emitted('update:open')).toEqual([[false]])
    wrapper.unmount()
  })
})

describe('列表同步', () => {
  it('同步时间排序与筛选组合传给后端，改变条件回到第一页', async () => {
    const wrapper = mount(ApplicationConfigList, { attachTo: document.body,
      global: { stubs: { NewConfigDrawer: true, PlatformInstanceDrawer: true, DriftCompareDrawer: true } },
    })
    await flushPromises()
    const sortHeader = wrapper.findAll('.ant-table-thead th').find((header) => header.text().includes('最近同步'))!
    expect(sortHeader.find('.ant-table-column-sorters').exists()).toBe(true)
    await sortHeader.find('.ant-table-column-sorters').trigger('click')
    await flushPromises()
    expect(api.listConfigFiles).toHaveBeenLastCalledWith(expect.objectContaining({
      sort_by: 'last_synced_at', sort_order: 'asc', page: 1,
    }))
    const selects = wrapper.findAllComponents({ name: 'ASelect' }).filter((select) => select.classes().includes('config-filter'))
    expect(selects).toHaveLength(2)
    const table = wrapper.findComponent({ name: 'ATable' })
    table.vm.$emit('change', { current: 3, pageSize: 20 }, {}, { field: 'last_synced_at', order: 'ascend' })
    await flushPromises()
    selects[0]!.vm.$emit('update:value', 2)
    selects[0]!.vm.$emit('change', 2)
    await flushPromises()
    selects[1]!.vm.$emit('update:value', 'drifted')
    selects[1]!.vm.$emit('change', 'drifted')
    await flushPromises()
    expect(api.listConfigFiles).toHaveBeenLastCalledWith(expect.objectContaining({
      platform_instance_id: 2, status: 'drifted', sort_order: 'asc', page: 1,
    }))
    selects[0]!.vm.$emit('update:value', undefined)
    selects[0]!.vm.$emit('change', undefined)
    await flushPromises()
    expect(api.listConfigFiles).toHaveBeenLastCalledWith(expect.objectContaining({
      platform_instance_id: undefined, status: 'drifted',
    }))
    wrapper.unmount()
  })

  it('请求阶段及排队阶段分别保持各行加载，互不取消', async () => {
    const pending: Record<number, (value: unknown) => void> = {}
    const polls: Record<number, (value: unknown) => void> = {}
    api.syncConfigFile.mockImplementation((id: number) => new Promise((resolve) => { pending[id] = resolve }))
    api.getConfigTask.mockImplementation((id: number) => new Promise((resolve) => { polls[id] = resolve }))
    const wrapper = mount(ApplicationConfigList, { attachTo: document.body,
      global: { stubs: { NewConfigDrawer: true, PlatformInstanceDrawer: true, DriftCompareDrawer: true } },
    })
    await flushPromises()
    const syncButtons = () => [...document.querySelectorAll<HTMLButtonElement>('.ant-table-tbody button')]
      .filter((button) => button.textContent?.includes('同步'))
    expect(syncButtons()).toHaveLength(2)
    syncButtons()[0]!.click()
    syncButtons()[1]!.click()
    await flushPromises()
    expect(syncButtons().map((button) => button.classList.contains('ant-btn-loading'))).toEqual([true, true])
    pending[1]!(task(11, 1, 'queued'))
    await flushPromises()
    expect(syncButtons().map((button) => button.classList.contains('ant-btn-loading'))).toEqual([true, true])
    pending[2]!(task(12, 2, 'queued'))
    await flushPromises()
    polls[11]!(task(11, 1, 'success'))
    await flushPromises()
    expect(syncButtons().map((button) => button.classList.contains('ant-btn-loading'))).toEqual([false, true])
    wrapper.unmount()
  })
})

describe('配置编辑器', () => {
  it.each([
    ['yaml', 'app:\n  name: test'],
    ['json', '{\n  "name": "test"\n}'],
    ['consul_kv', '{\n  "key": "value"\n}'],
  ] as const)('%s 的缩进行显示引导线', async (format, modelValue) => {
    const structured = mount(ConfigContentEditor, { props: { format, modelValue, readonly: true } })
    await flushPromises()
    expect(structured.find('.cm-indent-guides').exists()).toBe(true)
    structured.unmount()
  })

  it('TEXT 仍使用普通输入框', () => {
    const plain = mount(ConfigContentEditor, { props: { format: 'text', modelValue: 'plain' } })
    expect(plain.find('textarea').exists()).toBe(true)
    plain.unmount()
  })
})

describe('新建配置默认格式与应用搜索', () => {
  it.each([
    ['nacos', 'YAML'], ['apollo', 'YAML'], ['consul', 'Consul KV'],
  ])('%s 手工与发现模式采用正确默认格式', async (provider, expected) => {
    api.listPlatformInstances.mockResolvedValue({ items: [{ id: 1, name: '平台', provider, enabled: true }] })
    const wrapper = mount(NewConfigDrawer, { props: { open: false, importTask: null }, attachTo: document.body })
    await wrapper.setProps({ open: true })
    await flushPromises()
    const vm = wrapper.vm as unknown as { instanceId: number; mode: string; selected: unknown[] }
    vm.instanceId = 1
    await flushPromises()
    const formatField = () => [...document.querySelectorAll<HTMLElement>('.ant-form-item')]
      .find((item) => item.querySelector('label')?.textContent === '格式')!
    expect(formatField().querySelector('.ant-select-selection-item')?.textContent).toBe(expected)
    vm.mode = 'discover'
    await flushPromises()
    vm.selected = [{ display_name: 'common.yaml', locator: { data_id: 'common.yaml' }, revision: null }]
    await flushPromises()
    expect(formatField().querySelector('.ant-select-selection-item')?.textContent).toBe(expected)
    wrapper.unmount()
  })

  it.each(['manual', 'discover'])('%s 模式按名称搜索并选中第 101 个应用', async (mode) => {
    api.listCmdbApplications.mockResolvedValue({ items: Array.from({ length: 105 }, (_, index) => ({
      id: index + 1, name: `service-${String(index).padStart(3, '0')}`,
    })) })
    const wrapper = mount(NewConfigDrawer, { props: { open: false, importTask: null }, attachTo: document.body })
    await wrapper.setProps({ open: true })
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      instanceId: number; mode: string; selected: unknown[];
      manual: { application_ids: number[] }; discovered: { application_ids: number[] }[];
    }
    vm.instanceId = 1
    vm.mode = mode
    await flushPromises()
    if (mode === 'discover') vm.selected = [
      { display_name: 'common.yaml', locator: { data_id: 'common.yaml' }, revision: null },
    ]
    await flushPromises()
    const field = [...document.querySelectorAll<HTMLElement>('.ant-form-item')]
      .find((item) => item.querySelector('label')?.textContent === '关联 CMDB 应用')!
    const search = field.querySelector<HTMLInputElement>('input')!
    search.focus()
    search.closest('.ant-select-selector')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    search.value = 'service-100'
    search.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    const option = [...document.querySelectorAll<HTMLElement>('.ant-select-item-option')]
      .find((item) => item.textContent === 'service-100')
    expect(option).toBeDefined()
    option!.click()
    await flushPromises()
    expect(mode === 'manual' ? vm.manual.application_ids : vm.discovered[0]!.application_ids).toEqual([101])
    wrapper.unmount()
  })
})

describe('Nacos 手工新建', () => {
  async function openNacos() {
    const wrapper = mount(NewConfigDrawer, { props: { open: false, importTask: null }, attachTo: document.body })
    await wrapper.setProps({ open: true })
    await flushPromises()
    ;(wrapper.vm as unknown as { instanceId: number | null }).instanceId = 1
    await flushPromises()
    return wrapper
  }

  it('选择 Nacos 后提供 Namespace 与当前命名空间的 Group 候选', async () => {
    const wrapper = await openNacos()
    expect(api.listNacosNamespaces).toHaveBeenCalledWith(1)
    const namespace = document.querySelector<HTMLInputElement>('.locator-fields input')!
    namespace.focus()
    namespace.closest('.ant-select-selector')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flushPromises()
    expect(document.body.textContent).toContain('stage (stage-id)')
    const stageOption = [...document.querySelectorAll<HTMLElement>('.ant-select-item-option')]
      .find((item) => item.textContent?.includes('stage (stage-id)'))!
    stageOption.click()
    await flushPromises()
    expect((wrapper.vm as unknown as { manual: { locator: Record<string, string> } }).manual.locator.namespace).toBe('stage-id')
    const group = document.querySelectorAll<HTMLInputElement>('.locator-fields input')[1]!
    group.focus()
    group.closest('.ant-select-selector')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flushPromises()
    expect(api.discoverConfigResources).toHaveBeenCalledWith(1, { tenant: 'stage-id' })
    expect(document.body.textContent).toContain('PAYMENT')
    expect(document.body.textContent).toContain('ORDERS')
    wrapper.unmount()
  })

  it('允许手工输入新 Group，并将默认 public 的空 Namespace ID 提交', async () => {
    api.createConfigFile.mockResolvedValue({ id: 17 })
    const wrapper = await openNacos()
    const manual = (wrapper.vm as unknown as { manual: { name: string; approval_role_id: number; initial_content: string } }).manual
    manual.name = 'new.yaml'
    manual.approval_role_id = 3
    manual.initial_content = 'key=value'
    const fields = document.querySelectorAll<HTMLInputElement>('.locator-fields input')
    const namespace = fields[0]!
    namespace.focus()
    namespace.closest('.ant-select-selector')!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flushPromises()
    const publicOption = [...document.querySelectorAll<HTMLElement>('.ant-select-item-option')]
      .find((item) => item.textContent?.includes('public'))
    publicOption!.click()
    for (const [index, value] of ['NEW_GROUP', 'new.yaml'].entries()) {
      const field = fields[index + 1]!
      field.value = value
      field.dispatchEvent(new Event('input', { bubbles: true }))
    }
    await flushPromises()
    expect((wrapper.vm as unknown as { manual: { locator: Record<string, string> } }).manual.locator)
      .toEqual({ namespace: '', group: 'NEW_GROUP', data_id: 'new.yaml' })
    ;(document.querySelector('.ant-modal-footer .ant-btn-primary') as HTMLButtonElement).click()
    await flushPromises()
    expect(api.createConfigFile).toHaveBeenCalledWith(1, expect.objectContaining({
      locator: { namespace: '', group: 'NEW_GROUP', data_id: 'new.yaml' },
    }))
    wrapper.unmount()
  })

  it('远端候选读取失败时仍保留可输入的 Namespace 与 Group', async () => {
    api.listNacosNamespaces.mockRejectedValue(new Error('unreachable'))
    api.discoverConfigResources.mockRejectedValue(new Error('unreachable'))
    const wrapper = await openNacos()
    expect(document.body.textContent).toContain('可手工输入')
    const fields = document.querySelectorAll<HTMLInputElement>('.locator-fields input')
    expect(fields[0]!.disabled).toBe(false)
    fields[0]!.value = 'custom-tenant'
    fields[0]!.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    fields[1]!.focus()
    await flushPromises()
    expect(fields[1]!.disabled).toBe(false)
    expect(document.body.textContent).toContain('可手工输入')
    wrapper.unmount()
  })

  it('切换实例后忽略旧实例返回的命名空间', async () => {
    let resolveFirst!: (value: unknown) => void
    api.listNacosNamespaces.mockImplementation((id: number) => id === 1
      ? new Promise((resolve) => { resolveFirst = resolve })
      : Promise.resolve({ items: [{ id: 'new-id', name: 'new' }] }))
    const wrapper = await openNacos()
    ;(wrapper.vm as unknown as { instanceId: number | null }).instanceId = 2
    await flushPromises()
    resolveFirst({ items: [{ id: 'stale-id', name: 'stale' }] })
    await flushPromises()
    document.querySelector<HTMLInputElement>('.locator-fields input')!.focus()
    document.querySelector<HTMLInputElement>('.locator-fields input')!.closest('.ant-select-selector')!
      .dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flushPromises()
    expect(document.body.textContent).toContain('new (new-id)')
    expect(document.body.textContent).not.toContain('stale (stale-id)')
    wrapper.unmount()
  })

  it('Namespace 候选可按显示名称筛选', async () => {
    api.listNacosNamespaces.mockResolvedValue({ items: [
      { id: '', name: 'public' }, { id: 'a96', name: 'stage' },
    ] })
    const wrapper = await openNacos()
    const namespace = document.querySelector<HTMLInputElement>('.locator-fields input')!
    namespace.value = 'stage'
    namespace.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    const options = [...document.querySelectorAll<HTMLElement>('.ant-select-item-option')]
      .map((option) => option.textContent)
    expect(options).toContain('stage (a96)')
    expect(options).not.toContain('public (默认)')
    wrapper.unmount()
  })
})
