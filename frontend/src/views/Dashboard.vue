<script setup lang="ts">
// 工作台：接口聚合快照按读取权限展示，30 秒静默刷新保留已有数据。
import { computed, onMounted, onUnmounted, ref, watch, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { ApartmentOutlined, AuditOutlined, CloudServerOutlined, FileTextOutlined, GlobalOutlined, ProfileOutlined } from '@ant-design/icons-vue'
import { fetchActivityTrend, fetchDashboardSummary, type ActivityTrendPoint, type DashboardAttention, type DashboardSummary, type TrendGranularity } from '@/api/dashboard'
import { useUserStore } from '@/stores/user'
import { fmtTime } from '@/views/ticket/meta'
import DashboardChart from './dashboard/DashboardChart.vue'

const router = useRouter()
const user = useUserStore()
const summary = ref<DashboardSummary | null>(null)
const summaryLoading = ref(true)
const summaryFailed = ref(false)
const updatedAt = ref<Date | null>(null)
const trendItems = ref<ActivityTrendPoint[]>([])
const granularity = ref<TrendGranularity>('day')
const trendLoading = ref(false)
const trendFailed = ref(false)
let stopped = false
let pollTimer: number | undefined
let trendRequest = 0
const can = (permission: string) => user.hasPerm(`${permission}:read`)
const cmdb = computed(() => can('cmdb') ? summary.value?.cmdb : null)
const domain = computed(() => can('domain') ? summary.value?.domain : null)
const config = computed(() => can('config') ? summary.value?.config : null)
const ticket = computed(() => can('ticket') ? summary.value?.ticket : null)
const execution = computed(() => can('execution') ? summary.value?.execution : null)
const audit = computed(() => can('audit') ? summary.value?.audit : null)
const canTrend = computed(() => can('ticket') || can('execution'))
const initialLoading = computed(() => summaryLoading.value && !summary.value)
const todayText = new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' })
const number = (value: number) => value.toLocaleString()
const colors = ['#4b7bf2', '#43b99c', '#57b8d9', '#9b80e7', '#efb24e', '#e98287', '#c7d1e1']
const configNames: Record<string, string> = { clean: '一致', drifted: '存在漂移', remote_missing: '远端缺失', sync_failed: '同步失败', unpublished: '待首次发布' }
const configColors = ['#43b99c', '#efb24e', '#e98287', '#ed9d77', '#c7d1e1']
const providerNames: Record<string, string> = { tencent_dnspod: '腾讯云 DNSPod', aws_route53: 'AWS Route 53', google_cloud_dns: 'Google Cloud DNS' }
const deployNames: Record<string, string> = { docker: 'Docker', k8s: 'Kubernetes', shell: 'Shell' }
const moduleNames: Record<string, string> = { config: '应用配置', application_config: '应用配置', domain: '域名管理', ticket: '工单', execution: '执行', cmdb: '资产', auth: '认证', user: '用户', role: '角色', credential: '凭据', template: '模板', notify: '通知', system: '系统', audit: '审计', job: '作业' }
const hostNames: Record<string, string> = { online: '在线', offline: '离线', maintenance: '维护' }
const environmentNames: Record<string, string> = { prod: '生产', stage: '预发布', demo: '演示' }
const periodOptions: { value: TrendGranularity; label: string; description: string }[] = [
  { value: 'day', label: '近30天', description: '最近 30 天 · 每日提交与完成数量' },
  { value: 'week', label: '近12周', description: '最近 12 周 · 每周提交与完成数量' },
  { value: 'month', label: '近12月', description: '最近 12 个月 · 每月提交与完成数量' },
  { value: 'year', label: '近5年', description: '最近 5 年 · 每年提交与完成数量' },
]
const periodDescription = computed(() => periodOptions.find(option => option.value === granularity.value)?.description)

/** 摘要失败只标记陈旧，避免轮询把已加载的业务快照清空。 */
async function loadSummary(silentTransportError = false) {
  if (summaryLoading.value && summary.value) return
  summaryLoading.value = true
  try {
    const result = await fetchDashboardSummary(silentTransportError)
    if (stopped) return
    summary.value = result
    updatedAt.value = new Date()
    summaryFailed.value = false
  } catch { if (!stopped) summaryFailed.value = true }
  finally { if (!stopped) summaryLoading.value = false }
}

/** 粒度请求递增编号，忽略切换后迟到的旧响应。 */
async function loadTrend() {
  const requestId = ++trendRequest
  if (!canTrend.value) { trendItems.value = []; trendLoading.value = false; return }
  trendLoading.value = true
  trendFailed.value = false
  trendItems.value = []
  try {
    const result = await fetchActivityTrend(granularity.value)
    if (!stopped && requestId === trendRequest) trendItems.value = result.items
  } catch { if (!stopped && requestId === trendRequest) trendFailed.value = true }
  finally { if (!stopped && requestId === trendRequest) trendLoading.value = false }
}
watch(granularity, loadTrend)
watch(canTrend, loadTrend)

interface Kpi { label: string; value: number; hint: string; icon: Component; color: string; path: string }
const kpis = computed<Kpi[]>(() => {
  const cards: Kpi[] = []
  if (cmdb.value) {
    const { host_status: status } = cmdb.value
    cards.push({ label: '主机总数', value: cmdb.value.host_total, hint: `在线 ${status.online ?? 0} · 离线 ${status.offline ?? 0} · 维护 ${status.maintenance ?? 0}`, icon: CloudServerOutlined, color: colors[0]!, path: '/cmdb/hosts' })
    cards.push({ label: '应用总数', value: cmdb.value.app_total, hint: 'CMDB 登记应用', icon: ApartmentOutlined, color: colors[1]!, path: '/cmdb/apps' })
  }
  if (domain.value) cards.push({ label: '托管 Zone', value: domain.value.zone_total, hint: `本地记录集 ${number(domain.value.record_total)}`, icon: GlobalOutlined, color: colors[2]!, path: '/domains' })
  if (config.value) cards.push({ label: '配置文件', value: config.value.file_total, hint: `存在漂移 ${config.value.status_dist.drifted ?? 0} · 同步失败 ${config.value.status_dist.sync_failed ?? 0}`, icon: ProfileOutlined, color: colors[3]!, path: '/application-configs' })
  if (ticket.value) cards.push({ label: '本月工单', value: ticket.value.month_total, hint: `今日提单 ${number(ticket.value.today_total)}`, icon: FileTextOutlined, color: colors[0]!, path: '/ticket/list' })
  if (summary.value?.todo_total != null) cards.push({ label: '待我审批', value: summary.value.todo_total, hint: `工单 ${summary.value.todo_breakdown.ticket} · 应用配置 ${summary.value.todo_breakdown.config}`, icon: AuditOutlined, color: colors[4]!, path: '/ticket/todo' })
  return cards
})
const loadingKpiCount = computed(() => (can('cmdb') ? 2 : 0) + (can('domain') ? 1 : 0) + (can('config') ? 1 : 0) + (can('ticket') ? 1 : 0) + 1)
function distribution(values: Record<string, number>, names: Record<string, string> = {}) {
  return Object.entries(values).map(([key, value], index) => ({ name: names[key] || key, value, color: colors[index % colors.length]! }))
}
const configItems = computed(() => Object.keys(configNames).map((key, index) => ({ name: configNames[key]!, value: config.value?.status_dist[key] ?? 0, color: configColors[index]! })))
const configRatio = computed(() => config.value?.file_total ? `${((config.value.status_dist.clean ?? 0) / config.value.file_total * 100).toFixed(1)}%` : '—')
const appItems = computed(() => distribution(cmdb.value?.app_deploy_type ?? {}, deployNames))
const providerItems = computed(() => distribution(domain.value?.provider_dist ?? {}, providerNames))
const recordItems = computed(() => distribution(domain.value?.record_type_dist ?? {}))
const auditItems = computed(() => distribution(audit.value?.module_dist ?? {}, moduleNames))
const auditResultNames: Record<string, string> = { success: '成功', failed: '失败', failure: '失败' }
const auditResults = computed(() => distribution(audit.value?.result_dist ?? {}, auditResultNames))
const environments = computed(() => [...new Set(cmdb.value?.host_environment_status.map(item => item.environment) ?? [])])
const hostStates = computed(() => [...new Set(['online', 'offline', 'maintenance', ...(cmdb.value?.host_environment_status.map(item => item.status) ?? [])])])
const hostSeries = computed(() => hostStates.value.map((status, index) => ({
  name: hostNames[status] || status, color: [colors[0]!, colors[5]!, colors[4]!][index] || colors[index % colors.length]!,
  values: environments.value.map(environment => cmdb.value?.host_environment_status.filter(item => item.environment === environment && item.status === status).reduce((sum, item) => sum + item.count, 0) ?? 0),
})))
const ticketGroups = [
  { name: '审批中', statuses: ['approving'], color: colors[4]! },
  { name: '进行中', statuses: ['queued', 'running', 'paused'], color: colors[0]! },
  { name: '成功', statuses: ['success'], color: colors[1]! },
  { name: '异常', statuses: ['failed', 'interrupted'], color: colors[5]! },
  { name: '已关闭', statuses: ['rejected', 'cancelled', 'terminated'], color: colors[6]! },
]
const ticketItems = computed(() => {
  const dist = ticket.value?.status_dist ?? {}
  const result = ticketGroups.map(group => ({ name: group.name, value: group.statuses.reduce((sum, status) => sum + (dist[status] ?? 0), 0), color: group.color }))
  const known = new Set(ticketGroups.flatMap(group => group.statuses))
  const other = Object.entries(dist).filter(([status]) => !known.has(status)).reduce((sum, [, value]) => sum + value, 0)
  if (other) result.push({ name: '其他', value: other, color: colors[3]! })
  return result
})
const trendSeries = computed(() => {
  const result = []
  if (can('ticket')) result.push({ name: '提交工单', color: colors[0]!, values: trendItems.value.map(item => item.tickets) })
  if (can('execution')) result.push({ name: '完成执行', color: colors[1]!, values: trendItems.value.map(item => item.executions) })
  return result
})
const trendTotals = computed(() => trendSeries.value.map(series => ({ name: series.name, value: series.values.some(value => value !== null) ? number(series.values.reduce<number>((sum, value) => sum + (value ?? 0), 0)) : '—' })))
const syncTotal = computed(() => Object.values(domain.value?.sync_status ?? {}).reduce((sum, value) => sum + value, 0))
const syncRatio = computed(() => syncTotal.value ? `${((domain.value?.sync_status.success ?? 0) / syncTotal.value * 100).toFixed(1)}%` : '—')
const syncNames: Record<string, string> = { success: '同步成功', failed: '同步失败', pending: '待同步', syncing: '同步中' }
const syncColors: Record<string, string> = { success: colors[1]!, failed: colors[5]!, syncing: colors[0]!, pending: colors[4]! }
const syncItems = computed(() => Object.entries(domain.value?.sync_status ?? {}).map(([status, value]) => ({ name: syncNames[status] || status, value, color: syncColors[status] || colors[6]! })))
const alerts = computed(() => {
  const result: { label: string; value: number; color: string }[] = []
  if (execution.value) result.push({ label: '执行中', value: execution.value.active.running ?? 0, color: colors[0]! })
  if (config.value) {
    result.push({ label: '配置漂移', value: config.value.status_dist.drifted ?? 0, color: colors[4]! })
    result.push({ label: '配置同步失败', value: config.value.status_dist.sync_failed ?? 0, color: colors[5]! })
  }
  if (domain.value) result.push({ label: '域名同步失败', value: domain.value.sync_status.failed ?? 0, color: colors[5]! })
  return result
})
const attention = computed(() => (summary.value?.attention ?? []).filter(item => can(item.module)))
const hasAttentionAccess = computed(() => can('config') || can('domain') || can('execution'))
function attentionPath(item: DashboardAttention) { return `/${{ config: 'application-configs', domain: 'domains', execution: 'executions' }[item.module]}/${item.id}` }
function attentionStatus(item: DashboardAttention) {
  if (item.module === 'config') return configNames[item.status] || item.status
  if (item.module === 'domain') return syncNames[item.status] || item.status
  return ({ failed: '执行失败', interrupted: '执行中断' } as Record<string, string>)[item.status] || item.status
}
onMounted(() => {
  void loadSummary()
  void loadTrend()
  pollTimer = window.setInterval(() => { if (!document.hidden && !summaryLoading.value) void loadSummary(true) }, 30_000)
})
onUnmounted(() => { stopped = true; ++trendRequest; if (pollTimer) window.clearInterval(pollTimer) })
</script>

<template>
  <div class="dashboard dashboard-fonts">
    <header class="dashboard-welcome">
      <div><h1>工作台</h1><p>资源规模、业务执行与配置状态总览</p></div>
      <div class="dashboard-date"><b>{{ todayText }}</b><span>资产与配置为当前快照，趋势按图表周期统计</span></div>
    </header>
    <div v-if="initialLoading" role="status" aria-label="正在加载工作台" class="loading-banner"><a-spin />正在加载工作台…</div>
    <div v-if="summaryFailed" class="error-banner" role="alert">
      <span>{{ summary ? '更新失败，当前展示上次成功加载的数据' : '暂时无法加载工作台，请稍后重试' }}</span>
      <button data-testid="summary-retry" @click="loadSummary()">重试</button>
    </div>
    <div v-if="initialLoading" class="data-stats" :style="{ '--kpi-count': loadingKpiCount, '--kpi-tablet': Math.min(loadingKpiCount, 3), '--kpi-mobile': Math.min(loadingKpiCount, 2) }">
      <div v-for="index in loadingKpiCount" :key="index" class="data-stat skeleton-card"><span /><strong /><small /></div>
    </div>
    <div v-else-if="kpis.length" class="data-stats" :style="{ '--kpi-count': kpis.length, '--kpi-tablet': Math.min(kpis.length, 3), '--kpi-mobile': Math.min(kpis.length, 2) }">
      <button v-for="kpi in kpis" :key="kpi.label" class="data-stat" data-testid="dashboard-kpi" @click="router.push(kpi.path)">
        <div class="data-stat-label"><span>{{ kpi.label }}</span><component :is="kpi.icon" :style="{ color: kpi.color }" /></div>
        <strong>{{ number(kpi.value) }}</strong><small>{{ kpi.hint }}</small>
      </button>
    </div>
    <div v-if="summary && alerts.length" class="data-alerts">
      <span v-for="alert in alerts" :key="alert.label" :style="{ '--alert-color': alert.color }"><i />{{ alert.label }} <b>{{ alert.value }}</b></span>
      <a v-if="hasAttentionAccess" href="#dashboard-attention">查看关注事项 →</a>
    </div>
    <div v-if="initialLoading" class="loading-panels" aria-hidden="true"><div v-for="index in 3" :key="index" class="data-panel skeleton-panel" /></div>
    <template v-else-if="summary">
      <div v-if="canTrend || config" class="data-grid-top" :class="{ 'single-panel': !canTrend || !config }">
        <section v-if="canTrend" class="data-panel trend-panel">
          <div class="data-head"><div><h2>工单与执行趋势</h2><p>{{ periodDescription }}</p></div><div class="trend-tabs" aria-label="趋势周期"><button v-for="option in periodOptions" :key="option.value" :data-granularity="option.value" :aria-pressed="granularity === option.value" :class="{ active: granularity === option.value }" @click="granularity = option.value">{{ option.label }}</button></div></div>
          <div class="trend-key"><span v-for="series in trendSeries" :key="series.name" class="chart-legend"><i :style="{ background: series.color }" />{{ series.name }}</span><span v-if="!trendLoading" class="trend-total" data-testid="trend-total"><span v-for="item in trendTotals" :key="item.name">{{ item.name }} <b>{{ item.value }}</b></span></span></div>
          <DashboardChart kind="line" label="工单提交与完成执行数量趋势" :categories="trendItems.map(item => item.period)" :series="trendSeries" :loading="trendLoading" :failed="trendFailed" />
          <div class="data-foot trend-caption"><span>单位：笔 / 次</span><span v-if="can('execution')">完成执行按结束时间统计</span><button v-if="trendFailed" @click="loadTrend">重新加载趋势</button></div>
        </section>
        <section v-if="config" class="data-panel">
          <div class="data-head"><div><h2>应用配置状态</h2><p>有效配置文件 · 按当前状态归组</p></div><span class="data-scope">当前快照</span></div>
          <div class="config-strip"><div><strong>{{ number(config.file_total) }}</strong><small>有效配置文件</small></div><span><b>{{ configRatio }}</b> 一致</span></div>
          <DashboardChart kind="bar" label="应用配置五类状态数量" :items="configItems" />
          <div class="data-foot">按最近一次同步结果统计 · 不含已归档</div>
        </section>
      </div>
      <div v-if="cmdb || domain" class="data-grid-three">
        <section v-if="cmdb" class="data-panel">
          <div class="data-head"><div><h2>主机环境与状态</h2><p>按环境统计主机规模及登记状态</p></div><span class="data-scope">当前快照</span></div>
          <div class="mini-key"><span v-for="series in hostSeries" :key="series.name" class="chart-legend"><i :style="{ background: series.color }" />{{ series.name }}</span></div>
          <DashboardChart kind="stack" label="各环境主机登记状态数量" :categories="environments.map(environment => environmentNames[environment] || environment || '未设置')" :series="hostSeries" />
          <div class="data-foot">CMDB 登记状态 · 非实时监控指标</div>
        </section>
        <section v-if="cmdb" class="data-panel">
          <div class="data-head"><div><h2>应用部署方式</h2><p>按部署方式统计应用数量</p></div><span class="data-scope">当前快照</span></div>
          <DashboardChart kind="donut" label="应用部署方式数量分布" :items="appItems" total-label="应用总数" />
        </section>
        <section v-if="domain" class="data-panel domain-panel">
          <div class="data-head"><div><h2>域名资源与同步</h2><p>已接入供应商 · 本地快照数据</p></div><span class="data-scope">当前快照</span></div>
          <div class="dns-summary"><div><strong>{{ number(domain.zone_total) }}</strong><small>托管 Zone</small></div><div><strong>{{ number(domain.record_total) }}</strong><small>本地记录集</small></div><div class="dns-ok"><strong>{{ syncRatio }}</strong><small>最近同步成功率</small></div></div>
          <DashboardChart kind="bar" label="各 DNS 供应商托管 Zone 数量" :items="providerItems" />
          <div class="dns-status"><span v-for="item in syncItems" :key="item.name"><i :style="{ background: item.color }" />{{ item.name }} <b>{{ item.value }}</b></span></div>
        </section>
      </div>
      <div v-if="ticket || domain || audit" class="data-grid-three">
        <section v-if="ticket" class="data-panel">
          <div class="data-head"><div><h2>工单状态分布</h2><p>全量工单 · 按业务状态归组</p></div><span class="data-scope">全部工单</span></div>
          <DashboardChart kind="donut" label="工单业务状态数量分布" :items="ticketItems" total-label="全部工单" />
        </section>
        <section v-if="domain" class="data-panel">
          <div class="data-head"><div><h2>DNS 记录类型</h2><p>归一化记录集数量，不按记录值计数</p></div><span class="data-scope">当前快照</span></div>
          <DashboardChart kind="donut" label="本地 DNS 记录集类型分布" :items="recordItems" total-label="本地记录集" />
        </section>
        <section v-if="audit" class="data-panel audit-panel">
          <div class="data-head"><div><h2>审计模块分布</h2><p>按模块统计操作次数</p></div><span class="data-scope">今日</span></div>
          <div class="audit-summary"><strong>{{ number(audit.today_total) }} <small>次操作</small></strong><span><span v-for="item in auditResults" :key="item.name">{{ item.name }} <b>{{ item.value }}</b> </span></span></div>
          <DashboardChart kind="bar" label="今日各模块审计操作数量" :items="auditItems" />
        </section>
      </div>
      <section v-if="hasAttentionAccess" id="dashboard-attention" class="data-panel attention-panel">
        <div class="data-head"><div><h2>关注事项</h2><p>从执行、配置与域名中查看需要跟进的记录</p></div><span class="data-scope">最近记录</span></div>
        <div v-if="!attention.length" class="attention-empty">暂无需要关注的记录</div>
        <div v-else class="events-scroll"><table class="events"><thead><tr><th>模块</th><th>对象</th><th>最近更新</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in attention" :key="`${item.module}-${item.id}`"><td>{{ item.module === 'execution' ? '工单执行' : moduleNames[item.module] }}</td><td class="attention-name">{{ item.name }}</td><td>{{ item.updated_at ? fmtTime(item.updated_at) : '—' }}</td><td><span class="status-tag" :class="{ warning: item.status === 'drifted' }">{{ attentionStatus(item) }}</span></td><td><a :href="attentionPath(item)" @click.prevent="router.push(attentionPath(item))">{{ item.module === 'execution' ? '查看日志' : '查看详情' }} →</a></td></tr></tbody></table></div>
      </section>
    </template>
    <footer class="data-footer"><span>{{ updatedAt ? `更新于 ${updatedAt.toLocaleTimeString('zh-CN')}` : '数据按用户权限展示' }} · CMDB 状态与同步结果均不代表实时监控</span><div><a v-if="user.hasPerm('ticket:write')" href="/ticket/list" @click.prevent="router.push('/ticket/list')">提交工单</a><a href="/ticket/todo" @click.prevent="router.push('/ticket/todo')">待办审批</a></div></footer>
  </div>
</template>

<style scoped src="./dashboard/dashboard.css" />
