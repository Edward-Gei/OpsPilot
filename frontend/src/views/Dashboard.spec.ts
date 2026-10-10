// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'
import * as echarts from 'echarts/core'
import type { DashboardSummary } from '@/api/dashboard'
import Dashboard from './Dashboard.vue'

const api = vi.hoisted(() => ({ summary: vi.fn(), trend: vi.fn(), permissions: new Set<string>(), push: vi.fn(), theme: { mode: 'light' }, resizeCallbacks: [] as (() => void)[] }))
vi.mock('@/api/dashboard', () => ({ fetchDashboardSummary: api.summary, fetchActivityTrend: api.trend, fetchTicketTrend: api.trend }))
vi.mock('@/api/http', () => ({ request: vi.fn().mockResolvedValue({ pong: true }) }))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({ hasPerm: (p: string) => api.permissions.has(p), userInfo: { display_name: '管理员' } }) }))
vi.mock('@/stores/theme', () => ({ useThemeStore: () => reactive(api.theme) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: api.push }) }))
vi.mock('echarts/core', () => ({ use: vi.fn(), init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })) }))

const allPermissions = ['cmdb:read', 'domain:read', 'config:read', 'ticket:read', 'execution:read', 'audit:read']
function data(): { [Key in keyof DashboardSummary]: NonNullable<DashboardSummary[Key]> } {
  return {
    cmdb: { host_total: 12, app_total: 3, host_status: { online: 10, offline: 1, maintenance: 1 }, host_environment_status: [{ environment: 'prod', status: 'online', count: 10 }], app_deploy_type: { docker: 1, k8s: 1, shell: 1 } },
    domain: { zone_total: 5, record_total: 16, provider_dist: { tencent_dnspod: 3, aws_route53: 1, google_cloud_dns: 1 }, sync_status: { success: 3, failed: 1, syncing: 1 }, record_type_dist: { A: 10, TXT: 6 } },
    config: { file_total: 8, status_dist: { clean: 4, drifted: 1, remote_missing: 1, sync_failed: 1, unpublished: 1 } },
    ticket: { today_total: 1, month_total: 7, month_success: 4, month_finished: 5, status_dist: { success: 4, failed: 1, approving: 2 } },
    todo_total: 2, todo_breakdown: { ticket: 1, config: 1 }, execution: { active: { running: 1 }, recent: [] },
    audit_today: 11, audit: { today_total: 11, result_dist: { success: 10, failed: 1 }, module_dist: { config: 5, ticket: 4, audit: 2 } },
    attention: [{ module: 'config', id: 21, name: 'payment.yaml', status: 'drifted', updated_at: null }, { module: 'domain', id: 31, name: 'example.com', status: 'failed', updated_at: null }, { module: 'execution', id: 41, name: '发布执行', status: 'failed', updated_at: null }],
  }
}
let wrapper: VueWrapper | undefined
function render() { wrapper = mount(Dashboard, { global: { stubs: { ASpin: true } } }); return wrapper }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }

beforeEach(() => {
  vi.clearAllMocks()
  api.permissions = new Set(allPermissions)
  reactive(api.theme).mode = 'light'
  api.resizeCallbacks = []
  api.summary.mockResolvedValue(data())
  api.trend.mockResolvedValue({ granularity: 'day', items: [{ period: '10-09', tickets: 2, executions: 1 }] })
  vi.stubGlobal('ResizeObserver', class { constructor(callback: () => void) { api.resizeCallbacks.push(callback) } observe() {} disconnect() {} })
  Object.defineProperty(document, 'fonts', { configurable: true, value: { ready: Promise.resolve() } })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.useRealTimers() })

it('以接口数据呈现六项指标、八张图表及真实供应商名称', async () => {
  const w = render(); await flushPromises()
  expect(w.findAll('[data-testid="dashboard-kpi"]')).toHaveLength(6)
  expect(w.findAll('[data-testid="dashboard-chart"]')).toHaveLength(8)
  for (const title of ['工单与执行趋势', '应用配置状态', '主机环境与状态', '应用部署方式', '域名资源与同步', '工单状态分布', 'DNS 记录类型', '审计模块分布', 'DNSPod', 'Route 53', 'Cloud DNS']) expect(w.text()).toContain(title)
  expect(w.text()).not.toContain('Cloudflare')
  expect(w.findAll('[data-testid="dashboard-kpi"]')[0]!.text()).toContain('12')
})

it('真实 k8s 部署方式与审计模块使用中文映射，同步状态颜色保持业务语义', async () => {
  const w = render(); await flushPromises()
  expect(w.text()).toContain('Kubernetes')
  expect(w.text()).toContain('审计：2')
  const syncing = w.findAll('.dns-status span').find(item => item.text().includes('同步中'))!
  expect((syncing.find('i').element as HTMLElement).style.backgroundColor).toBe('rgb(75, 123, 242)')
})

it('九类 DNS 图例与十四个审计模块撑开图表，少项配置图保留默认高度', async () => {
  const many = data()
  many.domain.record_type_dist = Object.fromEntries(['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'CAA', 'SRV', 'SOA', 'NS'].map(type => [type, 1]))
  many.domain.record_total = 9
  many.audit.module_dist = Object.fromEntries(['auth', 'cmdb', 'ticket', 'config', 'domain', 'job', 'audit', 'user', 'role', 'system', 'notify', 'credential', 'template', 'execution'].map(module => [module, 1]))
  many.audit.today_total = 14
  api.summary.mockResolvedValue(many)
  const w = render(); await flushPromises()
  const panel = (title: string) => w.findAll('.data-panel').find(item => item.find('h2').text() === title)!
  const dns = panel('DNS 记录类型').find('[data-testid="dashboard-chart"]')
  const audit = panel('审计模块分布').find('[data-testid="dashboard-chart"]')
  expect(dns.findAll('.donut-legend>div')).toHaveLength(9)
  expect((dns.element as HTMLElement).style.height).toBe('257px')
  expect((audit.element as HTMLElement).style.height).toBe('336px')
  expect((panel('应用配置状态').find('[data-testid="dashboard-chart"]').element as HTMLElement).style.height).toBe('')
})

it('关注事项链接直达各模块详情', async () => {
  const w = render(); await flushPromises()
  for (const path of ['/application-configs/21', '/domains/31', '/executions/41']) expect(w.find(`a[href="${path}"]`).exists()).toBe(true)
})

it('即使响应含数据也按本地权限裁剪，且无工单执行权限不请求趋势', async () => {
  api.permissions.clear()
  const w = render(); await flushPromises()
  expect(api.trend).not.toHaveBeenCalled()
  expect(w.findAll('[data-testid="dashboard-chart"]')).toHaveLength(0)
  expect(w.text()).not.toContain('payment.yaml')
  expect(w.text()).not.toContain('example.com')
  expect(w.text()).not.toContain('发布执行')
  expect(w.text()).not.toContain('主机总数')
})

it('只有执行读取权限也请求趋势并隐藏工单系列', async () => {
  api.permissions = new Set(['execution:read'])
  const w = render(); await flushPromises()
  expect(api.trend).toHaveBeenCalledWith('day')
  expect(w.findAll('[data-testid="dashboard-chart"]')).toHaveLength(1)
  expect(w.text()).not.toContain('提交工单')
  expect(w.text()).toContain('完成执行')
})

it('首屏展示加载状态，摘要失败提供重试入口', async () => {
  const wait = deferred<ReturnType<typeof data>>()
  api.summary.mockReturnValueOnce(wait.promise)
  const w = render()
  expect(w.find('[aria-label="正在加载工作台"]').exists()).toBe(true)
  wait.resolve(data()); await flushPromises()
  expect(w.find('[aria-label="正在加载工作台"]').exists()).toBe(false)
  w.unmount(); wrapper = undefined
  api.summary.mockRejectedValueOnce(new Error('offline'))
  const failed = render(); await flushPromises()
  expect(failed.text()).toContain('暂时无法加载工作台')
  await failed.find('[data-testid="summary-retry"]').trigger('click'); await flushPromises()
  expect(failed.findAll('[data-testid="dashboard-kpi"]')).toHaveLength(6)
})

it('空数据展示图表空态，零分母不生成无效百分比', async () => {
  const empty = data()
  empty.cmdb = { host_total: 0, app_total: 0, host_status: {}, host_environment_status: [], app_deploy_type: {} }
  empty.domain = { zone_total: 0, record_total: 0, provider_dist: {}, sync_status: {}, record_type_dist: {} }
  empty.config = { file_total: 0, status_dist: {} }
  empty.ticket.status_dist = {} as typeof empty.ticket.status_dist
  empty.audit = { today_total: 0, result_dist: {}, module_dist: {} }
  empty.attention = []
  api.summary.mockResolvedValue(empty); api.trend.mockResolvedValue({ items: [] })
  const w = render(); await flushPromises()
  expect(w.findAll('[data-testid="chart-empty"]')).toHaveLength(8)
  expect(w.text()).toContain('暂无需要关注的记录')
  expect(w.text()).not.toMatch(/NaN|Infinity/)
})

it('全零趋势提示本周期无活动，有业务记录后提示消失', async () => {
  api.trend.mockResolvedValueOnce({ items: [{ period: '10-09', tickets: 0, executions: 0 }] })
  const w = render(); await flushPromises()
  expect(w.find('[data-testid="chart-zero-trend"]').text()).toContain('本周期暂无工单或完成执行')
  expect(w.find('[data-testid="chart-zero-trend"]').attributes('data-testid')).not.toBe('chart-empty')
  await w.find('button[data-granularity="week"]').trigger('click'); await flushPromises()
  expect(w.find('[data-testid="chart-zero-trend"]').exists()).toBe(false)
  expect(w.find('[data-testid="trend-total"]').text()).toContain('提交工单 2')
})

it('快速切换趋势时旧响应不能覆盖新粒度', async () => {
  const older = deferred<{ items: { period: string; tickets: number; executions: number }[] }>()
  api.trend.mockReturnValueOnce(older.promise)
  const w = render(); await flushPromises()
  api.trend.mockResolvedValueOnce({ items: [{ period: '2026-W40', tickets: 90, executions: 70 }] })
  await w.find('button[data-granularity="week"]').trigger('click'); await flushPromises()
  expect(w.find('[data-testid="trend-total"]').text()).toContain('90')
  older.resolve({ items: [{ period: 'old', tickets: 999, executions: 999 }] }); await flushPromises()
  expect(w.find('[data-testid="trend-total"]').text()).not.toContain('999')
})

it('后台不轮询，静默刷新失败保留旧数据并标记，卸载清理定时器', async () => {
  vi.useFakeTimers()
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  const w = render(); await flushPromises()
  Object.defineProperty(document, 'hidden', { configurable: true, value: true })
  await vi.advanceTimersByTimeAsync(30_000)
  expect(api.summary).toHaveBeenCalledTimes(1)
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  api.summary.mockRejectedValueOnce(new Error('offline'))
  await vi.advanceTimersByTimeAsync(30_000); await flushPromises()
  expect(api.summary).toHaveBeenLastCalledWith(true)
  expect(w.text()).toContain('更新失败')
  expect(w.findAll('[data-testid="dashboard-kpi"]')).toHaveLength(6)
  w.unmount(); wrapper = undefined
  await vi.advanceTimersByTimeAsync(60_000)
  expect(api.summary).toHaveBeenCalledTimes(2)
})

it('主题切换重绘所有图表，容器变化调整尺寸，卸载销毁实例', async () => {
  const w = render(); await flushPromises()
  const charts = vi.mocked(echarts.init).mock.results.map(result => result.value) as {
    setOption: ReturnType<typeof vi.fn>; resize: ReturnType<typeof vi.fn>; dispose: ReturnType<typeof vi.fn>
  }[]
  expect(charts).toHaveLength(8)
  for (const chart of charts) { chart.setOption.mockClear(); chart.resize.mockClear() }
  reactive(api.theme).mode = 'dark'; await nextTick(); await flushPromises()
  for (const chart of charts) expect(chart.setOption).toHaveBeenCalled()
  for (const callback of api.resizeCallbacks) callback()
  for (const chart of charts) expect(chart.resize).toHaveBeenCalled()
  w.unmount(); wrapper = undefined
  for (const chart of charts) expect(chart.dispose).toHaveBeenCalledTimes(1)
})
