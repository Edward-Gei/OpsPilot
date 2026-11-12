// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Antd, { Table } from 'ant-design-vue'
import UserList from './UserList.vue'

const api = vi.hoisted(() => ({ listUsers: vi.fn(), listRoles: vi.fn() }))
const permissions = vi.hoisted(() => ({ values: [] as string[] }))
vi.mock('@/api/system', () => api)
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: (perm: string) => permissions.values.includes(perm) }) }))

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

beforeEach(() => {
  vi.clearAllMocks()
  permissions.values = []
  api.listRoles.mockResolvedValue({ items: [] })
})

it('清除搜索词立即查询第一页，普通输入仍由回车提交', async () => {
  api.listUsers.mockResolvedValue({ items: [], total: 0 })
  const wrapper = mount(UserList, { global: { plugins: [Antd] } })
  await flushPromises()
  const listCalls = () => api.listUsers.mock.calls.filter(([params]) => params.page_size === 20)
  const input = wrapper.get<HTMLInputElement>('input[placeholder="搜索列表内容"]')
  await input.setValue('operate')
  expect(listCalls()).toHaveLength(1)
  await input.trigger('keydown.enter')
  await flushPromises()
  expect(listCalls()[listCalls().length - 1][0]).toEqual(expect.objectContaining({ page: 1, keyword: 'operate' }))
  const beforeClear = listCalls().length
  await wrapper.get('.toolbar .ant-input-clear-icon').trigger('click')
  await flushPromises()
  expect(listCalls()).toHaveLength(beforeClear + 1)
  expect(listCalls()[listCalls().length - 1][0]).toEqual(expect.objectContaining({ page: 1, keyword: undefined }))
  wrapper.unmount()
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

it.each([
  { permissions: ['user:write', 'user:mfa'], buttons: ['编辑', '重置密码', '禁用', 'MFA'] },
  { permissions: ['user:write'], buttons: ['编辑', '重置密码', '禁用'] },
  { permissions: ['user:mfa'], buttons: ['MFA'] },
])('操作列按 $permissions 保留按钮，其他列仍可拖拽调宽', async (scenario) => {
  permissions.values = scenario.permissions
  api.listUsers.mockResolvedValue({ items: [user(1, [operator])], total: 1 })
  const wrapper = mount(UserList, { global: { plugins: [Antd] } })
  await flushPromises()

  const row = wrapper.get('.ant-table-tbody tr.ant-table-row')
  expect(row.findAll('button').map(button => button.text().replace(/\s/g, ''))).toEqual(scenario.buttons)
  const table = wrapper.findComponent(Table)
  const columns = table.props('columns')!
  const action = columns.find(column => column.key === 'action')!
  expect(action.fixed).toBe('right')
  const username = columns.find(column => column.key === 'username')!
  expect(username.resizable).toBe(true)
  expect(typeof username.width).toBe('number')
  table.vm.$emit('resizeColumn', 192, username)
  await flushPromises()
  expect(table.props('columns')!.find(column => column.key === 'username')!.width).toBe(192)
  expect(action.fixed).toBe('right')
  wrapper.unmount()
})
