<script setup lang="ts">
// 工作台：真实业务数据总览（概览 KPI / 提单数趋势 / 执行动态 / 工单状态分布 / 快捷入口）
// 数据源：GET /dashboard/summary（30s 轮询）+ GET /dashboard/ticket-trend（粒度切换单独拉取）
// 各分区按 summary 中对应段是否为 null（即权限点）自动显隐
import { computed, nextTick, onMounted, onUnmounted, ref, watch, type Component } from 'vue'
import { useRouter } from 'vue-router'
import {
  ApartmentOutlined,
  AuditOutlined,
  BellOutlined,
  CodeOutlined,
  DatabaseOutlined,
  FileAddOutlined,
  FileDoneOutlined,
  LoadingOutlined,
  RocketOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
} from '@ant-design/icons-vue'
import * as echarts from 'echarts/core'
import { LineChart, PieChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { request } from '@/api/http'
import {
  fetchDashboardSummary,
  fetchTicketTrend,
  type DashboardSummary,
  type TrendGranularity,
  type TrendPoint,
} from '@/api/dashboard'
import { useUserStore } from '@/stores/user'
import { useThemeStore } from '@/stores/theme'
import { execStatusMeta } from '@/views/execution/meta'
import { fmtTime } from '@/views/ticket/meta'

echarts.use([LineChart, PieChart, GridComponent, TooltipComponent, CanvasRenderer])

const router = useRouter()
const userStore = useUserStore()
const themeStore = useThemeStore()

// ===== 概览数据与轮询 =====
const summary = ref<DashboardSummary | null>(null)
const trendItems = ref<TrendPoint[]>([])
const granularity = ref<TrendGranularity>('day')
const apiStatus = ref<'checking' | 'up' | 'down'>('checking')
const summaryLoading = ref(true)
const trendLoading = ref(true)
let pollTimer: number | undefined
// 卸载标志：onMounted 中 await 之后组件可能已被卸载（快速切路由），
// 若不检查会在卸载后启动 setInterval 导致定时器泄漏
let unmounted = false

async function loadSummary() {
  summaryLoading.value = true
  try {
    summary.value = await fetchDashboardSummary()
    apiStatus.value = 'up'
  } catch {
    apiStatus.value = 'down'
  } finally {
    summaryLoading.value = false
  }
}

async function loadTrend() {
  if (!userStore.hasPerm('ticket:read')) {
    trendLoading.value = false
    return
  }
  trendLoading.value = true
  try {
    trendItems.value = (await fetchTicketTrend(granularity.value)).items
  } catch {
    /* 全局拦截器已提示 */
  } finally {
    trendLoading.value = false
  }
}

const initialLoading = computed(() => summary.value === null && summaryLoading.value)
const dashboardLoading = computed(
  () => initialLoading.value || (!!summary.value?.ticket && trendLoading.value),
)
const canViewCmdb = computed(() => userStore.hasPerm('cmdb:read'))
const canViewTicket = computed(() => userStore.hasPerm('ticket:read'))
const canViewExecution = computed(() => userStore.hasPerm('execution:read'))
const canViewApproval = computed(() => userStore.hasPerm('ticket:approve'))
const canViewAudit = computed(() => userStore.hasPerm('audit:read'))
const loadingKpiCount = computed(() => {
  let count = canViewCmdb.value ? 2 : 0
  if (canViewTicket.value) count += 1
  if (canViewApproval.value || canViewExecution.value || canViewAudit.value) count += 1
  return Math.min(count, 4)
})
const loadingHeroStatCount = computed(() => Math.max(
  1,
  [canViewApproval.value, canViewExecution.value, canViewTicket.value].filter(Boolean).length,
))
const showMainSkeleton = computed(() => canViewExecution.value || canViewTicket.value)

// ===== 问候横幅 =====
const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 18) return '下午好'
  return '晚上好'
})
const roleNames = computed(
  () => (userStore.userInfo?.roles ?? []).map((r) => r.name).join(' / ') || '—',
)
const todayText = new Date().toLocaleDateString('zh-CN', {
  year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
})

/** 横幅右侧快览：待审批 / 执行中 / 今日工单（无权限段自动缺席） */
const heroStats = computed(() => {
  const s = summary.value
  if (!s) return []
  const list: { label: string; value: number; path: string }[] = []
  if (s.todo_total !== null) list.push({ label: '待我审批', value: s.todo_total, path: '/ticket/todo' })
  if (s.execution) list.push({ label: '执行中', value: s.execution.active.running ?? 0, path: '/executions' })
  if (s.ticket) list.push({ label: '今日工单', value: s.ticket.today_total, path: '/ticket/list' })
  return list
})

// ===== KPI 渐变卡（按权限动态组卡，最多 4 张） =====
interface KpiCard {
  grad: string
  icon: Component
  num: string
  label: string
  trend: string
  path: string
}
const kpis = computed<KpiCard[]>(() => {
  const s = summary.value
  if (!s) return []
  const cards: KpiCard[] = []
  if (s.cmdb) {
    const hs = s.cmdb.host_status
    cards.push({
      grad: 'blue', icon: DatabaseOutlined, num: String(s.cmdb.host_total), label: '主机总数',
      trend: `在线 ${hs.online ?? 0} · 离线 ${hs.offline ?? 0} · 维护 ${hs.maintenance ?? 0}`,
      path: '/cmdb/hosts',
    })
    cards.push({
      grad: 'green', icon: ApartmentOutlined, num: String(s.cmdb.app_total), label: '应用总数',
      trend: '应用与主机多对多关联', path: '/cmdb/apps',
    })
  }
  if (s.ticket) {
    const t = s.ticket
    const rate = t.month_finished > 0 ? `${Math.round((t.month_success / t.month_finished) * 100)}%` : '—'
    cards.push({
      grad: 'purple', icon: FileDoneOutlined, num: String(t.month_total), label: '本月工单',
      trend: `执行成功率 ${rate} · 今日 +${t.today_total}`, path: '/ticket/list',
    })
  }
  // 第四张按角色侧重：审批人看待办，运维看进行中执行，审计员看今日审计
  if (s.todo_total !== null && s.todo_total > 0) {
    cards.push({
      grad: 'orange', icon: AuditOutlined, num: String(s.todo_total), label: '待我审批',
      trend: '点击进入待办处理', path: '/ticket/todo',
    })
  } else if (s.execution) {
    const a = s.execution.active
    cards.push({
      grad: 'orange', icon: RocketOutlined,
      num: String((a.queued ?? 0) + (a.running ?? 0) + (a.paused ?? 0)), label: '进行中执行',
      trend: `排队 ${a.queued ?? 0} · 执行 ${a.running ?? 0} · 暂停 ${a.paused ?? 0}`,
      path: '/executions',
    })
  } else if (s.audit_today !== null) {
    cards.push({
      grad: 'orange', icon: SafetyCertificateOutlined, num: String(s.audit_today),
      label: '今日审计条目', trend: '操作全量留痕 · 按月分区', path: '/audit',
    })
  }
  return cards.slice(0, 4)
})

// ===== 工单状态分布（9 态归并 5 组，与 ticket/meta 语义一致） =====
const DIST_GROUPS: { name: string; statuses: string[]; color: string }[] = [
  { name: '审批中', statuses: ['approving'], color: '#fbbf24' },
  { name: '进行中', statuses: ['queued', 'running', 'paused'], color: '#3b82f6' },
  { name: '成功', statuses: ['success'], color: '#22c55e' },
  { name: '异常', statuses: ['failed', 'interrupted'], color: '#f87171' },
  { name: '已关闭', statuses: ['rejected', 'cancelled'], color: '#94a3b8' },
]
const distData = computed(() => {
  const dist = summary.value?.ticket?.status_dist ?? {}
  return DIST_GROUPS.map((g) => ({
    name: g.name,
    value: g.statuses.reduce((acc, st) => acc + (dist[st] ?? 0), 0),
    itemStyle: { color: g.color },
  }))
})
const ticketTotal = computed(() => distData.value.reduce((acc, d) => acc + d.value, 0))

// ===== ECharts：环形图 + 趋势折线（主题切换时重绘取色） =====
const pieRef = ref<HTMLDivElement>()
const trendRef = ref<HTMLDivElement>()
let pieChart: echarts.ECharts | undefined
let trendChart: echarts.ECharts | undefined

/** 读取当前主题下的 CSS 变量值（图表颜色跟随双主题） */
function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function renderPie() {
  if (!pieRef.value) return
  pieChart = pieChart ?? echarts.init(pieRef.value)
  pieChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}：{c} 单（{d}%）' },
    series: [{
      type: 'pie',
      radius: ['58%', '80%'],
      center: ['50%', '50%'],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: cssVar('--bg-card'), borderWidth: 2 },
      label: { show: false },
      emphasis: { scaleSize: 4 },
      data: distData.value.filter((d) => d.value > 0),
    }],
  }, true)
}

function renderTrend() {
  if (!trendRef.value) return
  trendChart = trendChart ?? echarts.init(trendRef.value)
  const primary = cssVar('--primary') || '#3b82f6'
  trendChart.setOption({
    grid: { left: 8, right: 16, top: 24, bottom: 0, containLabel: true },
    tooltip: { trigger: 'axis', formatter: '{b}<br/>提单 {c} 笔' },
    xAxis: {
      type: 'category',
      data: trendItems.value.map((i) => i.period),
      boundaryGap: false,
      axisLine: { lineStyle: { color: cssVar('--border') } },
      axisLabel: { color: cssVar('--text-3'), fontSize: 11 },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: cssVar('--border'), type: 'dashed' } },
      axisLabel: { color: cssVar('--text-3'), fontSize: 11 },
    },
    series: [{
      type: 'line',
      data: trendItems.value.map((i) => i.count),
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      showSymbol: trendItems.value.length <= 12,
      lineStyle: { width: 2.5, color: primary },
      itemStyle: { color: primary },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: `${primary}55` },
            { offset: 1, color: `${primary}00` },
          ],
        },
      },
    }],
  }, true)
}

function resizeCharts() {
  pieChart?.resize()
  trendChart?.resize()
}

// 数据/主题变化后重绘（nextTick 确保容器已随 v-if 渲染）
// 趋势图额外监听卡片可见性：首屏并发拉取时若趋势数据先于 summary 返回，
// 容器尚未渲染会跳过绘制，需在 summary 到达后补绘，否则首次进入图表空白
watch([distData, () => themeStore.mode], () => nextTick(renderPie))
watch(
  [trendItems, () => !!summary.value?.ticket, () => themeStore.mode],
  () => nextTick(renderTrend),
)

// ===== 系统状态（保留 /ping 探活：nginx→api→MySQL/Redis 全链路） =====
const services = computed(() => [
  { abbr: 'My', grad: 'var(--grad-blue)', name: 'MySQL' },
  { abbr: 'Rd', grad: 'var(--grad-red)', name: 'Redis' },
  { abbr: 'Api', grad: 'var(--grad-green)', name: 'FastAPI' },
])

// ===== 快捷入口（按权限过滤，最多 8 个） =====
interface QuickLink {
  icon: Component
  color: string
  label: string
  path: string
  perm: string
}
const ALL_LINKS: QuickLink[] = [
  { icon: FileAddOutlined, color: '#60a5fa', label: '提交工单', path: '/ticket/list', perm: 'ticket:write' },
  { icon: AuditOutlined, color: '#fbbf24', label: '待办审批', path: '/ticket/todo', perm: 'ticket:approve' },
  { icon: DatabaseOutlined, color: '#4ade80', label: '主机管理', path: '/cmdb/hosts', perm: 'cmdb:read' },
  { icon: CodeOutlined, color: '#a78bfa', label: '模板管理', path: '/job/templates', perm: 'template:read' },
  { icon: RocketOutlined, color: '#38bdf8', label: '执行中心', path: '/executions', perm: 'execution:read' },
  { icon: BellOutlined, color: '#f472b6', label: '通知中心', path: '/notify', perm: 'notify:read' },
  { icon: SafetyCertificateOutlined, color: '#f87171', label: '安全审计', path: '/audit', perm: 'audit:read' },
  { icon: TeamOutlined, color: '#34d399', label: '用户管理', path: '/system/users', perm: 'user:read' },
]
const quickLinks = computed(() => ALL_LINKS.filter((l) => userStore.hasPerm(l.perm)).slice(0, 8))

// ===== 执行动态辅助 =====
/** 终态执行耗时（秒级人性化）；未结束显示 — */
function execDuration(startedAt: string | null, finishedAt: string | null): string {
  if (!startedAt || !finishedAt) return '—'
  const sec = Math.max(0, Math.round((+new Date(finishedAt) - +new Date(startedAt)) / 1000))
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m${sec % 60}s`
  return `${Math.floor(sec / 3600)}h${Math.floor((sec % 3600) / 60)}m`
}

onMounted(async () => {
  // 首屏：探活 + 概览 + 趋势并发拉取
  request<{ pong: boolean }>({ url: '/ping', method: 'get' }).catch(() => (apiStatus.value = 'down'))
  await Promise.all([loadSummary(), loadTrend()])
  if (unmounted) return // 组件已在等待期间卸载，不再启动轮询/绑定监听
  // 30s 静默轮询概览（页面隐藏时跳过，避免后台空耗）
  pollTimer = window.setInterval(() => {
    if (!document.hidden) loadSummary()
  }, 30_000)
  window.addEventListener('resize', resizeCharts)
})

onUnmounted(() => {
  unmounted = true
  if (pollTimer) window.clearInterval(pollTimer)
  window.removeEventListener('resize', resizeCharts)
  pieChart?.dispose()
  trendChart?.dispose()
})
</script>

<template>
  <div class="dashboard">
    <!-- A 问候横幅 -->
    <div class="op-hero op-hero--blue">
      <div class="op-hero-icon"><RocketOutlined /></div>
      <div>
        <div class="op-hero-title">{{ greeting }}，{{ userStore.userInfo?.display_name || userStore.userInfo?.username || '—' }}</div>
        <div class="op-hero-sub">{{ roleNames }} · {{ todayText }}</div>
      </div>
      <div class="op-hero-extra">
        <template v-if="initialLoading">
          <div
            v-for="slot in loadingHeroStatCount"
            :key="`hero-loading-${slot}`"
            class="op-hero-stat hero-stat-skeleton"
          >
            <b class="dashboard-skeleton-line hero-skeleton-value" />
            <span class="dashboard-skeleton-line hero-skeleton-label" />
          </div>
        </template>
        <template v-else>
          <div
            v-for="st in heroStats"
            :key="st.label"
            class="op-hero-stat hero-stat-click"
            @click="router.push(st.path)"
          >
            <b>{{ st.value }}</b>
            <span>{{ st.label }}</span>
          </div>
        </template>
        <div v-if="dashboardLoading" class="op-hero-loading" role="status" aria-label="正在加载工作台">
          <LoadingOutlined spin />
          <span>加载中</span>
        </div>
      </div>
    </div>

    <!-- B KPI 渐变卡（真实业务数，点击直达） -->
    <div
      v-if="initialLoading && loadingKpiCount"
      class="kpi-row"
      :style="{ gridTemplateColumns: `repeat(${loadingKpiCount}, 1fr)` }"
    >
      <div v-for="slot in loadingKpiCount" :key="`kpi-loading-${slot}`" class="kpi kpi-skeleton">
        <div class="kpi-icon dashboard-skeleton-line" />
        <div class="dashboard-skeleton-line kpi-skeleton-num" />
        <div class="dashboard-skeleton-line kpi-skeleton-label" />
        <div class="dashboard-skeleton-line kpi-skeleton-trend" />
      </div>
    </div>
    <div v-else-if="kpis.length" class="kpi-row" :style="{ gridTemplateColumns: `repeat(${kpis.length}, 1fr)` }">
      <div v-for="kpi in kpis" :key="kpi.label" class="kpi" :class="kpi.grad" @click="router.push(kpi.path)">
        <div class="kpi-icon"><component :is="kpi.icon" /></div>
        <div class="num">{{ kpi.num }}</div>
        <div class="label">{{ kpi.label }}</div>
        <div class="trend">{{ kpi.trend }}</div>
      </div>
    </div>

    <!-- C 提单趋势 + 状态分布/系统状态 -->
    <div v-if="initialLoading && showMainSkeleton" class="row-main dashboard-skeleton-row">
      <div v-if="canViewTicket" class="op-card dashboard-skeleton-card">
        <div class="card-head">
          <div>
            <div class="dashboard-skeleton-line dashboard-skeleton-title" />
            <div class="dashboard-skeleton-line dashboard-skeleton-subtitle" />
          </div>
          <div class="dashboard-skeleton-line dashboard-skeleton-action" />
        </div>
        <div class="dashboard-skeleton-chart" />
      </div>
      <div v-else-if="canViewExecution" class="op-card exec-card dashboard-skeleton-card">
        <div class="card-head">
          <div>
            <div class="dashboard-skeleton-line dashboard-skeleton-title" />
            <div class="dashboard-skeleton-line dashboard-skeleton-subtitle" />
          </div>
          <div class="dashboard-skeleton-line dashboard-skeleton-action" />
        </div>
        <div class="dashboard-skeleton-list">
          <div v-for="slot in 4" :key="`exec-loading-${slot}`" class="dashboard-skeleton-item">
            <span class="dashboard-skeleton-line dashboard-skeleton-dot" />
            <span class="dashboard-skeleton-line dashboard-skeleton-wide" />
            <span class="dashboard-skeleton-line dashboard-skeleton-short" />
          </div>
        </div>
      </div>

      <div class="side-col">
        <div v-if="canViewTicket" class="op-card dashboard-skeleton-card">
          <div class="dashboard-skeleton-line dashboard-skeleton-title" />
          <div class="dashboard-skeleton-line dashboard-skeleton-subtitle" />
          <div class="dashboard-skeleton-pie" />
          <div class="dashboard-skeleton-legend">
            <span v-for="slot in 4" :key="`legend-loading-${slot}`" class="dashboard-skeleton-line" />
          </div>
        </div>
        <div class="op-card dashboard-skeleton-card">
          <div class="dashboard-skeleton-line dashboard-skeleton-title" />
          <div class="dashboard-skeleton-line dashboard-skeleton-subtitle" />
          <div class="dashboard-skeleton-services">
            <span v-for="slot in 3" :key="`service-loading-${slot}`" class="dashboard-skeleton-line" />
          </div>
        </div>
      </div>
    </div>
    <div class="row-main" v-else-if="summary?.execution || summary?.ticket">
      <div v-if="summary?.ticket" class="op-card">
        <div class="card-head">
          <div>
            <div class="op-card-title">提单数趋势</div>
            <div class="op-card-sub">
              {{ { day: '近 30 天', week: '近 12 周', month: '近 12 个月', year: '近 5 年' }[granularity] }}按提交时间统计
            </div>
          </div>
          <a-segmented
            v-model:value="granularity"
            :options="[
              { label: '天', value: 'day' },
              { label: '周', value: 'week' },
              { label: '月', value: 'month' },
              { label: '年', value: 'year' },
            ]"
            @change="loadTrend"
          />
        </div>
        <div class="trend-chart-shell">
          <div ref="trendRef" class="trend-chart" />
          <div v-if="trendLoading" class="trend-loading" role="status" aria-label="正在加载提单趋势">
            <LoadingOutlined spin />
          </div>
        </div>
      </div>

      <div v-else-if="summary?.execution" class="op-card exec-card">
        <div class="card-head">
          <div>
            <div class="op-card-title">执行动态</div>
            <div class="op-card-sub">最近执行记录 · 30 秒自动刷新</div>
          </div>
          <a class="more-link" @click="router.push('/executions')">全部执行 →</a>
        </div>
        <div v-if="summary.execution.recent.length" class="exec-list">
          <div
            v-for="e in summary.execution.recent"
            :key="e.id"
            class="exec-row"
            @click="router.push(`/executions/${e.id}`)"
          >
            <span class="dot" :class="e.status" />
            <div class="exec-main">
              <b>{{ e.ticket_no }} · {{ e.title }}</b>
              <span>{{ e.job_host_name || '—' }} · {{ e.total_steps }} 步骤 · {{ e.creator_name }}</span>
            </div>
            <div class="exec-meta">
              <span class="exec-time">{{ fmtTime(e.created_at) }}</span>
              <span class="exec-dur">{{ execDuration(e.started_at, e.finished_at) }}</span>
              <a-tag :color="execStatusMeta[e.status as keyof typeof execStatusMeta]?.color">
                {{ execStatusMeta[e.status as keyof typeof execStatusMeta]?.text || e.status }}
              </a-tag>
            </div>
          </div>
        </div>
        <div v-else class="exec-empty">
          <a-empty description="暂无执行记录" :image-style="{ height: '72px' }" />
        </div>
      </div>

      <div class="side-col">
        <div v-if="summary?.ticket" class="op-card">
          <div class="op-card-title">工单状态分布</div>
          <div class="op-card-sub">全部工单 {{ ticketTotal }} 笔按状态归组</div>
          <div v-if="ticketTotal" class="pie-wrap">
            <div ref="pieRef" class="pie-chart" />
            <div class="pie-center">
              <b>{{ ticketTotal }}</b>
              <span>工单总数</span>
            </div>
          </div>
          <a-empty v-else description="暂无工单" :image-style="{ height: '56px' }" style="margin: 24px 0" />
          <div v-if="ticketTotal" class="pie-legend">
            <div v-for="d in distData" :key="d.name" class="legend-item">
              <span class="legend-dot" :style="{ background: d.itemStyle.color }" />
              <span class="legend-name">{{ d.name }}</span>
              <b>{{ d.value }}</b>
            </div>
          </div>
        </div>

        <div class="op-card">
          <div class="op-card-title">系统状态</div>
          <div class="op-card-sub">nginx → api → MySQL / Redis 全链路探活</div>
          <div class="svc-row">
            <div v-for="svc in services" :key="svc.name" class="svc-mini">
              <span class="svc-abbr" :style="{ background: svc.grad }">{{ svc.abbr }}</span>
              <span class="svc-name">{{ svc.name }}</span>
              <span class="pill" :class="apiStatus === 'up' ? 'ok' : apiStatus === 'down' ? 'bad' : ''">
                {{ apiStatus === 'checking' ? '检测中' : apiStatus === 'up' ? '正常' : '异常' }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- D 执行动态 + 快捷入口 -->
    <div v-if="initialLoading && canViewExecution && canViewTicket" class="row-main dashboard-skeleton-row">
      <div class="op-card exec-card dashboard-skeleton-card">
        <div class="card-head">
          <div>
            <div class="dashboard-skeleton-line dashboard-skeleton-title" />
            <div class="dashboard-skeleton-line dashboard-skeleton-subtitle" />
          </div>
          <div class="dashboard-skeleton-line dashboard-skeleton-action" />
        </div>
        <div class="dashboard-skeleton-list">
          <div v-for="slot in 4" :key="`exec-bottom-loading-${slot}`" class="dashboard-skeleton-item">
            <span class="dashboard-skeleton-line dashboard-skeleton-dot" />
            <span class="dashboard-skeleton-line dashboard-skeleton-wide" />
            <span class="dashboard-skeleton-line dashboard-skeleton-short" />
          </div>
        </div>
      </div>
      <div class="op-card quick-card dashboard-skeleton-card">
        <div class="dashboard-skeleton-line dashboard-skeleton-title" />
        <div class="dashboard-skeleton-line dashboard-skeleton-subtitle" />
        <div class="quick-grid dashboard-skeleton-quick-grid">
          <div v-for="slot in 4" :key="`quick-loading-${slot}`" class="quick-item dashboard-skeleton-quick">
            <span class="dashboard-skeleton-line dashboard-skeleton-quick-icon" />
            <span class="dashboard-skeleton-line dashboard-skeleton-quick-label" />
          </div>
        </div>
      </div>
    </div>
    <div class="row-main" v-else>
      <div v-if="summary?.execution && summary?.ticket" class="op-card exec-card">
        <div class="card-head">
          <div>
            <div class="op-card-title">执行动态</div>
            <div class="op-card-sub">最近执行记录 · 30 秒自动刷新</div>
          </div>
          <a class="more-link" @click="router.push('/executions')">全部执行 →</a>
        </div>
        <div v-if="summary.execution.recent.length" class="exec-list">
          <div
            v-for="e in summary.execution.recent"
            :key="e.id"
            class="exec-row"
            @click="router.push(`/executions/${e.id}`)"
          >
            <span class="dot" :class="e.status" />
            <div class="exec-main">
              <b>{{ e.ticket_no }} · {{ e.title }}</b>
              <span>{{ e.job_host_name || '—' }} · {{ e.total_steps }} 步骤 · {{ e.creator_name }}</span>
            </div>
            <div class="exec-meta">
              <span class="exec-time">{{ fmtTime(e.created_at) }}</span>
              <span class="exec-dur">{{ execDuration(e.started_at, e.finished_at) }}</span>
              <a-tag :color="execStatusMeta[e.status as keyof typeof execStatusMeta]?.color">
                {{ execStatusMeta[e.status as keyof typeof execStatusMeta]?.text || e.status }}
              </a-tag>
            </div>
          </div>
        </div>
        <div v-else class="exec-empty">
          <a-empty description="暂无执行记录" :image-style="{ height: '72px' }" />
        </div>
      </div>

      <div class="op-card quick-card">
        <div class="op-card-title">快捷入口</div>
        <div class="op-card-sub">按你的权限展示可用功能</div>
        <div class="quick-grid">
          <div v-for="l in quickLinks" :key="l.path + l.label" class="quick-item" @click="router.push(l.path)">
            <span
              class="quick-icon"
              :style="{ color: l.color, background: `color-mix(in srgb, ${l.color} 13%, transparent)` }"
            >
              <component :is="l.icon" />
            </span>
            <span>{{ l.label }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ===== 首屏加载占位：保持最终布局尺寸，数据到达后只替换内容 ===== */
.dashboard-skeleton-line {
  display: block;
  flex-shrink: 0;
  border-radius: 6px;
  background: linear-gradient(90deg, var(--bg-hover), var(--bg-input), var(--bg-hover));
  background-size: 200% 100%;
  animation: dashboard-skeleton-shimmer 1.6s ease-in-out infinite;
}
@keyframes dashboard-skeleton-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
.hero-stat-skeleton {
  pointer-events: none;
}
.hero-skeleton-value {
  width: 24px;
  height: 18px;
  margin: 0 auto;
}
.hero-skeleton-label {
  width: 44px;
  height: 9px;
  margin: 5px auto 0;
}
.op-hero-loading {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 7px 10px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.14);
  color: rgba(255, 255, 255, 0.9);
  font-size: 12px;
  white-space: nowrap;
}
.op-hero-loading .anticon {
  font-size: 14px;
}
.kpi.kpi-skeleton {
  background: var(--bg-card);
  box-shadow: var(--shadow-card);
  color: var(--text-2);
  cursor: default;
}
.kpi.kpi-skeleton::before,
.kpi.kpi-skeleton::after {
  display: none;
}
.kpi-skeleton .kpi-icon {
  background: var(--bg-hover);
}
.kpi-skeleton-num {
  width: 42px;
  height: 30px;
  margin-top: 12px;
}
.kpi-skeleton-label {
  width: 62px;
  height: 10px;
  margin-top: 6px;
}
.kpi-skeleton-trend {
  width: 118px;
  height: 8px;
  margin-top: 9px;
}
.dashboard-skeleton-card {
  pointer-events: none;
}
.dashboard-skeleton-title {
  width: 88px;
  height: 15px;
}
.dashboard-skeleton-subtitle {
  width: 148px;
  height: 10px;
  margin-top: 7px;
}
.dashboard-skeleton-action {
  width: 62px;
  height: 22px;
}
.dashboard-skeleton-chart {
  height: 240px;
  margin-top: 8px;
  border-radius: 8px;
  background: linear-gradient(180deg, var(--bg-hover), transparent);
}
.dashboard-skeleton-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 16px;
}
.dashboard-skeleton-item {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 42px;
  padding: 10px 12px;
  border-radius: 12px;
  background: var(--bg-hover);
}
.dashboard-skeleton-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.dashboard-skeleton-wide {
  width: 42%;
  height: 10px;
}
.dashboard-skeleton-short {
  width: 64px;
  height: 9px;
  margin-left: auto;
}
.dashboard-skeleton-pie {
  width: 132px;
  height: 132px;
  margin: 18px auto 16px;
  border: 18px solid var(--bg-hover);
  border-radius: 50%;
  background: transparent;
  box-shadow: 0 0 0 1px var(--bg-input);
}
.dashboard-skeleton-legend {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px 14px;
}
.dashboard-skeleton-legend .dashboard-skeleton-line {
  width: 100%;
  height: 9px;
}
.dashboard-skeleton-services {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 16px;
}
.dashboard-skeleton-services .dashboard-skeleton-line {
  width: 100%;
  height: 42px;
  border-radius: 10px;
}
.trend-chart-shell {
  position: relative;
}
.trend-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--primary);
  background: color-mix(in srgb, var(--bg-card) 72%, transparent);
  pointer-events: none;
}
.trend-loading .anticon {
  font-size: 24px;
}
.dashboard-skeleton-quick-grid {
  pointer-events: none;
}
.dashboard-skeleton-quick {
  cursor: default;
}
.dashboard-skeleton-quick-icon {
  width: 32px;
  height: 32px;
  border-radius: 9px;
}
.dashboard-skeleton-quick-label {
  width: 54px;
  height: 9px;
}

/* ===== 横幅右侧快览可点击 ===== */
.hero-stat-click {
  cursor: pointer;
  transition: background 0.15s, transform 0.15s;
}
.hero-stat-click:hover {
  background: rgba(255, 255, 255, 0.26);
  transform: translateY(-1px);
}

/* ===== KPI 渐变卡（沿用 M0 视觉，改为可点击） ===== */
.kpi-row {
  display: grid;
  gap: 16px;
}
.kpi {
  border-radius: var(--radius-card);
  padding: 20px;
  color: #fff;
  position: relative;
  overflow: hidden;
  min-height: 128px;
  cursor: pointer;
  transition: transform 0.15s, filter 0.15s;
}
.kpi:hover {
  transform: translateY(-2px);
  filter: brightness(1.06);
}
.kpi::after {
  content: '';
  position: absolute;
  right: -28px;
  top: -28px;
  width: 110px;
  height: 110px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.12);
}
.kpi::before {
  content: '';
  position: absolute;
  right: 26px;
  bottom: -46px;
  width: 90px;
  height: 90px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.08);
}
.kpi.blue { background: var(--grad-blue); box-shadow: 0 8px 20px rgba(37, 99, 235, 0.28); }
.kpi.green { background: var(--grad-green); box-shadow: 0 8px 20px rgba(34, 197, 94, 0.28); }
.kpi.purple { background: var(--grad-purple); box-shadow: 0 8px 20px rgba(139, 92, 246, 0.28); }
.kpi.orange { background: var(--grad-orange); box-shadow: 0 8px 20px rgba(245, 158, 11, 0.28); }
.kpi-icon {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.2);
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 12px;
}
.kpi .num {
  font-size: 30px;
  font-weight: 800;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}
.kpi .label {
  font-size: 13px;
  opacity: 0.92;
  margin-top: 4px;
}
.kpi .trend {
  font-size: 11px;
  opacity: 0.85;
  margin-top: 8px;
}

/* ===== 主体两栏 ===== */
.row-main {
  display: grid;
  grid-template-columns: 1fr 340px;
  gap: 16px;
  align-items: start;
}
.side-col {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.more-link {
  font-size: 12px;
  color: var(--primary);
  flex-shrink: 0;
  cursor: pointer;
}

/* ===== 执行动态列表 ===== */
/* 卡片拉伸至与右侧列（状态分布+系统状态）等高并固定：
   列表 flex:1（basis 0）不参与撑高，空态居中填满、记录超高时内部滚动 */
.exec-card {
  align-self: stretch;
  min-height: 380px;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.exec-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
.exec-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
.exec-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 12px;
  background: var(--bg-hover);
  border: 1px solid transparent;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.exec-row:hover {
  border-color: var(--primary-soft);
  background: var(--primary-soft);
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--text-3);
}
.dot.success { background: var(--success); }
.dot.failed { background: var(--error); }
.dot.paused, .dot.terminated, .dot.interrupted { background: var(--warning); }
.dot.running {
  background: var(--primary);
  animation: pulse 1.6s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.45); }
  50% { box-shadow: 0 0 0 5px rgba(59, 130, 246, 0); }
}
@media (prefers-reduced-motion: reduce) {
  .dashboard-skeleton-line { animation: none; }
  .dashboard .anticon-spin { animation: none; }
  .dot.running { animation: none; }
  .kpi, .exec-row, .quick-item, .hero-stat-click { transition: none; }
}
.exec-main {
  flex: 1;
  min-width: 0;
}
.exec-main b {
  display: block;
  font-size: 13px;
  color: var(--text-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.exec-main span {
  display: block;
  font-size: 11px;
  color: var(--text-3);
  margin-top: 2px;
}
.exec-meta {
  display: grid;
  grid-template-columns: minmax(132px, auto) 52px 64px;
  align-items: center;
  justify-items: end;
  gap: 12px;
  flex-shrink: 0;
}
.exec-time {
  font-size: 11px;
  color: var(--text-3);
  text-align: right;
  white-space: nowrap;
}
.exec-dur {
  font-size: 11px;
  color: var(--text-2);
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.exec-meta :deep(.ant-tag) {
  margin-inline-end: 0;
  text-align: right;
}

/* ===== 环形图 ===== */
.pie-wrap {
  position: relative;
  height: 190px;
}
.pie-chart {
  width: 100%;
  height: 100%;
}
.pie-center {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}
.pie-center b {
  font-size: 24px;
  font-weight: 800;
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
}
.pie-center span {
  font-size: 11px;
  color: var(--text-3);
  margin-top: 2px;
}
.pie-legend {
  margin-top: 10px;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 6px 14px;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
}
.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.legend-name {
  color: var(--text-2);
  flex: 1;
}
.legend-item b {
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
}

/* ===== 系统状态（压缩行） ===== */
.svc-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.svc-mini {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 10px;
  background: var(--bg-hover);
}
.svc-abbr {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.svc-name {
  font-size: 12px;
  color: var(--text-1);
  flex: 1;
}
.pill {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 999px;
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--text-3);
  background: var(--bg-hover);
}
.pill::before {
  content: '';
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.pill.ok {
  color: var(--success);
  background: var(--success-soft);
}
.pill.bad {
  color: var(--error);
  background: color-mix(in srgb, var(--error) 12%, transparent);
}

/* ===== 趋势图 ===== */
.trend-chart {
  height: 240px;
  margin-top: 8px;
}

/* ===== 快捷入口 ===== */
.quick-card {
  align-self: stretch;
}
.quick-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}
.quick-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 12px;
  border-radius: 12px;
  background: var(--bg-hover);
  border: 1px solid transparent;
  font-size: 13px;
  color: var(--text-1);
  cursor: pointer;
  transition: transform 0.15s, border-color 0.15s;
}
.quick-item:hover {
  transform: translateY(-2px);
  border-color: var(--primary-soft);
}
.quick-icon {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  font-size: 15px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

/* ===== 响应式降级 ===== */
@media (max-width: 1200px) {
  .kpi-row { grid-template-columns: repeat(2, 1fr) !important; }
  .row-main { grid-template-columns: 1fr; }
  /* 单列堆叠时无右列可对齐，回退为固定最小高度内容自适应 */
  .exec-card { align-self: auto; min-height: 420px; }
}
@media (max-width: 768px) {
  .kpi-row { grid-template-columns: 1fr !important; }
  .exec-time { display: none; }
  .exec-meta { grid-template-columns: 52px 64px; }
  .quick-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
