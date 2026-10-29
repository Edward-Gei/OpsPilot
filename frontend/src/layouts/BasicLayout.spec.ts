// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
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
  id: 17, event, title: '配置通知', content: null, ref_type: refType, ref_id: refId,
  is_read: isRead, created_at: null,
})
let wrapper: ReturnType<typeof shallowMount> | undefined

beforeEach(() => {
  vi.clearAllMocks()
  api.getUnreadCount.mockResolvedValue({ count: 2 })
  api.markRead.mockResolvedValue(null)
})
afterEach(() => { wrapper?.unmount() })

async function openNotifications(item: ReturnType<typeof notification>) {
  api.listNotifications.mockResolvedValue({ items: [item], total: 1 })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
  ] })
  await router.push('/')
  wrapper = shallowMount(BasicLayout, { global: {
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
])('配置结果 %s 跳转关联配置详情，已读通知不重复减数', async (event, label) => {
  const { wrapper, router } = await openNotifications(notification(event, 'config_file', 23, true))
  await wrapper.find('.notif-item').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.fullPath).toBe('/application-configs/23')
  expect(wrapper.find('.notif-tag').text()).toBe(label)
  expect(api.markRead).not.toHaveBeenCalled()
  expect(wrapper.find('.notif-badge').text()).toBe('2')
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
