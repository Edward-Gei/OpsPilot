// @vitest-environment jsdom
import { expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import UserList from './UserList.vue'

const api = vi.hoisted(() => ({ listUsers: vi.fn() }))
vi.mock('@/api/system', () => api)
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: () => false }) }))

class TestResizeObserver { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal('ResizeObserver', TestResizeObserver)
const getComputedStyle = window.getComputedStyle.bind(window)
window.getComputedStyle = ((element: Element) => getComputedStyle(element)) as typeof window.getComputedStyle
window.matchMedia = vi.fn().mockImplementation((media: string) => ({
  matches: false, media, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
  addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
}))

const operator = { id: 5, code: 'ops', name: '运维人员' }
const auditor = { id: 9, code: 'auditor', name: '审计员' }
const user = (id: number, roles: typeof operator[]) => ({
  id, username: `user${id}`, display_name: `用户${id}`, status: 'active', source: 'local',
  mfa_enabled: false, mfa_bound: false, roles,
})

it('同一角色在不同行、角色顺序及分页变化后保持相同标签颜色', async () => {
  api.listUsers.mockImplementation(async ({ page, page_size }) => ({
    items: page_size === 1 ? [] : page === 1
      ? [user(1, [operator]), user(2, [auditor, operator])]
      : [user(3, [auditor, operator])],
    total: 21,
  }))
  const wrapper = mount(UserList, { global: { plugins: [Antd] } })
  await flushPromises()

  const operatorTags = () => [...wrapper.element.querySelectorAll('.ant-table-tbody .ant-tag') as NodeListOf<HTMLElement>]
    .filter((tag) => tag.textContent?.trim() === operator.name)
  expect(operatorTags()).toHaveLength(2)
  const initialColor = operatorTags()[0].className
  expect(operatorTags()[1].className).toBe(initialColor)

  const page = wrapper.vm as unknown as { onPageChange: (page: number, size: number) => void }
  page.onPageChange(2, 20)
  await flushPromises()
  expect(operatorTags()).toHaveLength(1)
  expect(operatorTags()[0].className).toBe(initialColor)
  wrapper.unmount()
})
