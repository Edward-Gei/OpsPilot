// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import BasicLayout from './BasicLayout.vue'

const api = vi.hoisted(() => ({
  getUnreadCount: vi.fn(), listNotifications: vi.fn(), markRead: vi.fn(), markAllRead: vi.fn(),
}))
vi.mock('@/api/notification', () => api)
vi.mock('@/api/applicationConfig', () => ({
  listConfigApprovalTodo: async () => ({ items: [], total: 0 }),
}))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({
  userInfo: null, hasPerm: () => false, hasAnyPerm: () => false,
}) }))
vi.mock('@/stores/theme', () => ({ useThemeStore: () => ({ mode: 'light' }) }))

const notification = (event: string, refType: string, refId: number, isRead = false) => ({
  id: 17, event, title: '配置通知', content: null as string | null, ref_type: refType, ref_id: refId,
  is_read: isRead, created_at: null,
})
let wrapper: ReturnType<typeof mount> | undefined

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }))
  api.getUnreadCount.mockResolvedValue({ count: 2 })
  api.markRead.mockResolvedValue(null)
})
afterEach(async () => {
  wrapper?.unmount()
  await flushPromises()
  vi.useRealTimers()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

async function openNotifications(item: ReturnType<typeof notification>) {
  api.listNotifications.mockResolvedValue({ items: [item], total: 1 })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
  ] })
  await router.push('/')
  wrapper = mount(BasicLayout, { attachTo: document.body, global: {
    plugins: [Antd, router], renderStubDefaultSlot: true,
    stubs: { ADropdown: { name: 'ADropdown', template: '<div><slot /><slot name="overlay" /></div>' }, RouterView: true },
  } })
  await flushPromises()
  wrapper.findAllComponents({ name: 'ADropdown' })[0]!.vm.$emit('openChange', true)
  await flushPromises()
  return { wrapper, router }
}

it('配置待审批通知进入配置审批页并同步已读和未读数', async () => {
  const { wrapper, router } = await openNotifications(notification('config.pending_approval', 'config_approval', 8))
  expect(wrapper.find('.notif-badge').text()).toBe('2')
  await wrapper.find('.notif-item').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.fullPath).toBe('/ticket/todo?tab=config')
  expect(wrapper.find('.notif-tag').text()).toBe('配置待审批')
  expect(api.markRead).toHaveBeenCalledWith(17)
  expect(wrapper.find('.notif-item').classes()).not.toContain('unread')
  expect(wrapper.find('.notif-badge').text()).toBe('1')
})

it.each([
  ['config.approved', '配置审批通过'],
  ['config.rejected', '配置审批驳回'],
  ['config.publish_success', '配置发布成功'],
  ['config.publish_failed', '配置发布失败'],
  ['config.sync_success', '配置同步成功'],
  ['config.sync_failed', '配置同步失败'],
])('配置结果 %s 跳转关联配置详情，已读通知不重复减数', async (event, label) => {
  const { wrapper, router } = await openNotifications(notification(event, 'config_file', 23, true))
  await wrapper.find('.notif-item').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.fullPath).toBe('/application-configs/23')
  expect(wrapper.find('.notif-tag').text()).toBe(label)
  expect(api.markRead).not.toHaveBeenCalled()
  expect(wrapper.find('.notif-badge').text()).toBe('2')
})

it('连续悬停通知满 1.5 秒才显示完整标题与多行正文，不标已读也不跳转', async () => {
  const title = '配置 mt-hello-world-production-service.yaml v16 发布成功，完整结果请查阅'
  const content = '配置文件 mt-hello-world-production-service.yaml v16 发布成功。\n任务 #168：<b>保留原始文本</b>'
  const { wrapper, router } = await openNotifications({
    ...notification('config.publish_success', 'config_file', 23), title, content,
  })
  vi.useFakeTimers()
  expect(document.querySelector('[role="tooltip"]')).toBeNull()
  await wrapper.find('.notif-item').trigger('mouseenter')
  await vi.advanceTimersByTimeAsync(1499)
  expect(document.querySelector('[role="tooltip"]')).toBeNull()
  await vi.advanceTimersByTimeAsync(1)
  const preview = document.querySelector('[role="tooltip"]')
  expect(preview).not.toBeNull()
  expect(preview!.textContent).toContain(title)
  expect(preview!.textContent).toContain(content)
  expect(document.querySelector('[role="tooltip"] b')).toBeNull()
  expect(api.markRead).not.toHaveBeenCalled()
  expect(wrapper.find('.notif-item').classes()).toContain('unread')
  expect(wrapper.find('.notif-badge').text()).toBe('2')
  expect(router.currentRoute.value.fullPath).toBe('/')
})

it('悬停未满 1.5 秒移开会取消预览，再次移入重新计时', async () => {
  const { wrapper } = await openNotifications(notification('config.sync_success', 'config_file', 23))
  vi.useFakeTimers()
  const item = wrapper.find('.notif-item')
  await item.trigger('mouseenter')
  await vi.advanceTimersByTimeAsync(1000)
  await item.trigger('mouseleave')
  await vi.advanceTimersByTimeAsync(2500)
  expect(document.querySelector('[role="tooltip"]')).toBeNull()
  await item.trigger('mouseenter')
  await vi.advanceTimersByTimeAsync(1499)
  expect(document.querySelector('[role="tooltip"]')).toBeNull()
  await vi.advanceTimersByTimeAsync(1)
  expect(document.querySelector('[role="tooltip"]')?.textContent).toBe('配置通知')
  expect(api.markRead).not.toHaveBeenCalled()
})

it('无正文通知悬停时仍可阅读完整标题', async () => {
  const title = '配置 mt-hello-world-production-service.yaml 等待第 16 个版本的审批'
  const { wrapper } = await openNotifications({
    ...notification('config.pending_approval', 'config_approval', 8), title,
  })
  vi.useFakeTimers()
  await wrapper.find('.notif-item').trigger('mouseenter')
  await vi.advanceTimersByTimeAsync(1500)
  expect(document.querySelector('[role="tooltip"]')?.textContent).toBe(title)
})

it('长多行通知提供视口内滚动区域，指针移入预览后保持展开', async () => {
  const content = Array.from({ length: 96 }, (_, index) => `第 ${index + 1} 行完整审批意见`).join('\n')
  const { wrapper } = await openNotifications({
    ...notification('config.rejected', 'config_file', 23), content,
  })
  vi.useFakeTimers()
  await wrapper.find('.notif-item').trigger('mouseenter')
  await vi.advanceTimersByTimeAsync(1500)
  const preview = document.querySelector<HTMLElement>('[role="tooltip"]')!
  expect(preview.textContent).toContain(content)
  expect(getComputedStyle(preview).overflowY).toBe('auto')
  expect(getComputedStyle(preview).maxHeight).toBe('calc(100vh - 48px)')
  await wrapper.find('.notif-item').trigger('mouseleave')
  const popup = preview.closest<HTMLElement>('.ant-tooltip')!
  popup.dispatchEvent(new MouseEvent('mouseenter'))
  await vi.advanceTimersByTimeAsync(150)
  expect(popup.classList.contains('ant-tooltip-hidden')).toBe(false)
  expect(api.markRead).not.toHaveBeenCalled()
})

it.each([
  ['ticket.approved', 'ticket', '/ticket/list?id=23'],
  ['execution.success', 'execution', '/executions/23'],
])('保留原有 %s 通知导航', async (event, refType, target) => {
  const { wrapper, router } = await openNotifications(notification(event, refType, 23))
  await wrapper.find('.notif-item').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.fullPath).toBe(target)
})
