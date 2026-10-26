// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import SettingsPage from './SettingsPage.vue'

const api = vi.hoisted(() => ({
  getConfigs: vi.fn(), listRoles: vi.fn(), updateConfigs: vi.fn(),
}))
vi.mock('@/api/system', () => api)

class TestResizeObserver { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal('ResizeObserver', TestResizeObserver)
window.matchMedia = vi.fn().mockImplementation((media: string) => ({
  matches: false, media, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
  addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  api.getConfigs.mockResolvedValue({ 'mfa.policy': 'off',
    'token.policy': { access_minutes: null, refresh_days: null } })
  api.listRoles.mockResolvedValue({ items: [] })
  api.updateConfigs.mockResolvedValue(null)
})

it('旧令牌策略显示默认 8 小时并保存可配置的会话上限', async () => {
  const wrapper = mount(SettingsPage, { global: { plugins: [Antd], stubs: { JobHostPanel: true } } })
  await flushPromises()
  expect(wrapper.text()).toContain('最长登录时长')
  expect(wrapper.text()).toContain('从登录时起算，持续操作不会延长')
  expect(wrapper.text()).not.toContain('Access Token')
  expect(wrapper.text()).not.toContain('Refresh Token')
  expect(wrapper.find('input[placeholder="默认 8"]').exists()).toBe(true)
  expect(wrapper.find('input[placeholder="默认 15"]').exists()).toBe(false)
  expect(wrapper.find('input[placeholder="默认 7"]').exists()).toBe(false)
  const page = wrapper.vm as unknown as {
    tokenPolicy: { session_hours: number }
    onSaveSecurity: () => Promise<void>
  }
  expect(page.tokenPolicy.session_hours).toBe(8)
  page.tokenPolicy.session_hours = 12
  await page.onSaveSecurity()
  expect(api.updateConfigs).toHaveBeenCalledWith(expect.objectContaining({
    'token.policy': { session_hours: 12 },
  }))
  wrapper.unmount()
})
