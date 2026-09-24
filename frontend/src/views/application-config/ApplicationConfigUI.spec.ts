// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { config, flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import { createPinia } from 'pinia'
import ConfigContentEditor from './ConfigContentEditor.vue'
import DriftCompareDrawer from './DriftCompareDrawer.vue'
import ApplicationConfigList from './ApplicationConfigList.vue'
import NewConfigDrawer from './NewConfigDrawer.vue'

const api = vi.hoisted(() => ({
  getConfigFile: vi.fn(), getDriftView: vi.fn(), listVersions: vi.fn(),
  importDrift: vi.fn(), publishVersion: vi.fn(), getConfigTask: vi.fn(),
  listConfigFiles: vi.fn(), listConfigImportTasks: vi.fn(), syncConfigFile: vi.fn(),
  listPlatformInstances: vi.fn(), listApprovalRoles: vi.fn(), listCmdbApplications: vi.fn(),
  listNacosNamespaces: vi.fn(), discoverConfigResources: vi.fn(), createConfigFile: vi.fn(),
}))
vi.mock('@/api/applicationConfig', () => api)
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: (perm: string) => perm === 'config:write' }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

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

describe('漂移确认', () => {
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
