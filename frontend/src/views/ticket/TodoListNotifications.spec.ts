// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import TodoList from './TodoList.vue'

vi.mock('@/api/applicationConfig', () => ({ listConfigApprovalTodo: async () => ({ items: [], total: 0 }) }))
vi.mock('@/api/ticket', () => ({ todoTickets: async () => ({ items: [], total: 0 }) }))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: () => true }) }))

let wrapper: ReturnType<typeof shallowMount> | undefined
beforeEach(() => { vi.clearAllMocks() })
afterEach(() => { wrapper?.unmount() })

async function openTodo(path: string) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/ticket/todo', component: { template: '<div />' } },
  ] })
  await router.push(path)
  wrapper = shallowMount(TodoList, { global: { plugins: [Antd, router] } })
  await flushPromises()
  return wrapper
}

it('从配置通知进入时优先展示配置审批，即使拥有工单审批权限', async () => {
  const wrapper = await openTodo('/ticket/todo?tab=config')
  expect(wrapper.findComponent({ name: 'ATabs' }).props('activeKey')).toBe('config')
})

it('普通进入时保留原有工单审批默认页签', async () => {
  const wrapper = await openTodo('/ticket/todo')
  expect(wrapper.findComponent({ name: 'ATabs' }).props('activeKey')).toBe('ticket')
})
