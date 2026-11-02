// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { config, flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import DomainDetail from './DomainDetail.vue'

const api = vi.hoisted(() => ({ getDomain: vi.fn(), listRecords: vi.fn() }))
vi.mock('@/api/domain', () => api)
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: () => false }) }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '1' } }),
  useRouter: () => ({ push: vi.fn() }),
}))
config.global.plugins = [Antd]
class TestResizeObserver { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal('ResizeObserver', TestResizeObserver)
const getComputedStyle = window.getComputedStyle.bind(window)
window.getComputedStyle = ((element: Element) => getComputedStyle(element)) as typeof window.getComputedStyle
window.matchMedia = vi.fn().mockImplementation((media: string) => ({ matches: false, media,
  addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }))

beforeEach(() => {
  vi.clearAllMocks()
  api.getDomain.mockResolvedValue({
    id: 1, provider: 'aws_route53', remote_zone_id: 'Z1', zone_name: 'example.com.',
    credential_name: null, description: null, record_count: 1, sync_status: 'success',
    last_synced_at: null, last_sync_error: null, created_at: null, updated_at: null,
  })
})
afterEach(() => { document.body.innerHTML = '' })

it.each([
  { recordType: 'NS', values: ['ns-1015.awsdns-62.net.', 'ns-1199.awsdns-21.org.', 'ns-161.awsdns-20.com.'] },
  { recordType: 'TXT', values: ['"v=spf1 include:example.com ~all"', '"verification=  original value"'] },
])('$recordType 记录值悬停框保留原始换行、空格和选区文本', async ({ recordType, values }) => {
  api.listRecords.mockResolvedValue({ total: 1, items: [{
    id: 1, record_name: 'example.com.', record_type: recordType, ttl: 300, values,
    read_only: true, read_only_reason: null, created_at: null, updated_at: null,
  }] })
  const wrapper = mount(DomainDetail, { attachTo: document.body })
  try {
    await flushPromises()
    await wrapper.get('.record-values').trigger('mouseenter')
    await vi.waitFor(() => {
      expect(document.querySelector('.ant-tooltip-inner')).not.toBeNull()
    })
    const tooltip = document.querySelector('.ant-tooltip-inner')!
    expect(tooltip.textContent).toBe(values.join('\n'))
    expect(window.getComputedStyle(tooltip).whiteSpace).toBe('pre-wrap')
    const range = document.createRange()
    range.selectNodeContents(tooltip)
    expect(range.toString()).toBe(values.join('\n'))
  } finally {
    wrapper.unmount()
  }
})
