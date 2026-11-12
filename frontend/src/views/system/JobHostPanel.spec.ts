// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import JobHostPanel from './JobHostPanel.vue'

const api = vi.hoisted(() => ({ listJobHosts: vi.fn() }))
vi.mock('@/api/jobHost', () => api)
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: () => false }) }))

class TestResizeObserver { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal('ResizeObserver', TestResizeObserver)
const getComputedStyle = window.getComputedStyle.bind(window)
window.getComputedStyle = ((element: Element) => getComputedStyle(element)) as typeof window.getComputedStyle
window.matchMedia = vi.fn().mockImplementation((media: string) => ({
  matches: false, media, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
  addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  api.listJobHosts.mockResolvedValue({ items: [], total: 0 })
})

it('清除作业主机搜索词立即查询第一页，输入仍需回车提交', async () => {
  const wrapper = mount(JobHostPanel, { global: { plugins: [Antd] } })
  await flushPromises()
  const input = wrapper.get<HTMLInputElement>('input[placeholder="搜索作业主机"]')
  await input.setValue('runner')
  expect(api.listJobHosts).toHaveBeenCalledTimes(1)
  await input.trigger('keydown.enter')
  await flushPromises()
  expect(api.listJobHosts).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, keyword: 'runner' }))
  const beforeClear = api.listJobHosts.mock.calls.length
  await wrapper.get('.toolbar .ant-input-clear-icon').trigger('click')
  await flushPromises()
  expect(api.listJobHosts).toHaveBeenCalledTimes(beforeClear + 1)
  expect(api.listJobHosts).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, keyword: undefined }))
  wrapper.unmount()
})
