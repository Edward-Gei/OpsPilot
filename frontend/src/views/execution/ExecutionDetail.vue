<script setup lang="ts">
// 执行详情页（M5）：标题横幅 + 流水线流程图 + 日志显示屏（作业主机上串行执行）
// 实时通道（纯长轮询，WS 已弃用）：
//   状态事件 = /events 长轮询（服务端挂起 30s，有事件立即返回）
//   日志追加 = /logs 每 2s 按 offset 增量拉取（Jenkins 式尾随效果）
// 弃用 WS 原因：WS 订阅状态易失效导致日志无法实时更新，
// 长轮询无连接状态、无订阅概念，按步骤偏移增量拉取合并展示，天然免疫该类 bug
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  ArrowLeftOutlined,
  CheckOutlined,
  CloseOutlined,
  LoadingOutlined,
  PauseCircleOutlined,
  PauseOutlined,
  PlayCircleOutlined,
  StopOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons-vue'
import * as execApi from '@/api/execution'
import { useUserStore } from '@/stores/user'
import { fmtTime } from '@/views/ticket/meta'
import { execStatusMeta, interruptReasonText, stepStatusMeta } from './meta'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const executionId = Number(route.params.id)

// ---------- 详情状态 ----------
const detail = ref<execApi.ExecutionDetail | null>(null)
const detailLoading = ref(false)
const showSkeleton = ref(false)
const SKELETON_DELAY = 160
let skeletonTimer: number | null = null

function clearSkeletonTimer() {
  if (skeletonTimer !== null) {
    window.clearTimeout(skeletonTimer)
    skeletonTimer = null
  }
}

function beginDetailLoading() {
  clearSkeletonTimer()
  detailLoading.value = true
  showSkeleton.value = false
  skeletonTimer = window.setTimeout(() => {
    if (detailLoading.value) showSkeleton.value = true
  }, SKELETON_DELAY)
}

/** 终态判定：终态后关闭实时通道，不再重连 */
const isFinished = computed(() =>
  ['success', 'failed', 'terminated', 'interrupted', 'rejected', 'cancelled'].includes(detail.value?.status ?? ''),
)

/** 初始加载 / 断线恢复：REST 拉全量详情（含步骤列表） */
async function loadDetail() {
  beginDetailLoading()
  try {
    const d = await execApi.getExecution(executionId)
    detail.value = d
    void fetchAllSegments()
  } finally {
    detailLoading.value = false
    showSkeleton.value = false
    clearSkeletonTimer()
  }
}

// ---------- 流水线步骤节点（点击定位到合并流中对应日志段） ----------
/** 步骤耗时：完成后按 started/finished 计算，未完成显示 — */
function fmtDuration(started: string | null, finished: string | null): string {
  if (!started || !finished) return '—'
  const ms = new Date(finished).getTime() - new Date(started).getTime()
  if (ms < 0) return '—'
  const s = Math.round(ms / 1000)
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}

// ---------- 合并日志流（全部步骤集中显示，按步骤分段管理缓冲与偏移） ----------
// 设计：后端日志按步骤落盘（/logs?step_order&offset），前端为每步骤维护一个
// LogSegment，按 step_order 顺序渲染为单一合并流；串行执行下同一时刻只有
// 1 个活跃段在增长，轮询请求量与旧"单步骤焦点"模式持平
interface LogSegment {
  step_order: number
  step_name: string
  lines: LogToken[][]
  offset: number // 该步骤下一次拉取的行偏移
  eof: boolean // 服务端已读到当前文件尾
  done: boolean // 步骤已终态且 eof：不再拉取
  truncated: boolean // 触发行数上限后段头部已丢弃较早日志
  dropped: number // 头部已丢弃行数（供渲染 key 计算绝对行号，裁剪时保留行 key 稳定）
}

type LogTokenKind = 'text' | 'timestamp' | 'info' | 'success' | 'warning' | 'error' | 'command' | 'path'

interface LogToken {
  text: string
  kind: LogTokenKind
}

const LOG_TOKEN_PATTERN = /(\$\s.*$)|(\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b)|(\/?(?:[\w.-]+\/)+(?:[\w.-]+)\b)|(\b(?:SUCCESS|SUCCEEDED|OK|DONE|COMPLETED)\b)|(\b(?:WARN|WARNING)\b)|(\b(?:ERROR|FAILED|FAILURE|FATAL|EXCEPTION)\b)|(\b(?:INFO|DEBUG|TRACE)\b)/gi

/** 将外部日志拆为纯文本令牌，供模板安全地按关键字着色。 */
function tokenizeLogLine(line: string): LogToken[] {
  const tokens: LogToken[] = []
  let offset = 0
  const kindByGroup: LogTokenKind[] = ['command', 'timestamp', 'path', 'success', 'warning', 'error', 'info']

  for (const match of line.matchAll(LOG_TOKEN_PATTERN)) {
    const index = match.index ?? 0
    if (index > offset) tokens.push({ text: line.slice(offset, index), kind: 'text' })
    const group = match.slice(1).findIndex(Boolean)
    tokens.push({ text: match[0], kind: kindByGroup[group] })
    offset = index + match[0].length
  }
  if (offset < line.length || !tokens.length) tokens.push({ text: line.slice(offset), kind: 'text' })
  return tokens
}

const segments = ref<LogSegment[]>([])
const logBox = ref<HTMLElement | null>(null)
const logReady = ref(false)
const logFetchError = ref(false)

type LogPhase = 'connecting' | 'waiting' | 'streaming' | 'retrying' | 'empty' | 'complete'

const hasLogLines = computed(() => segments.value.some((segment) => segment.lines.length > 0))
const logPhase = computed<LogPhase>(() => {
  if (logFetchError.value) return 'retrying'
  if (!logReady.value) return 'connecting'
  if (hasLogLines.value) return isFinished.value ? 'complete' : 'streaming'
  return isFinished.value ? 'empty' : 'waiting'
})
const logPhaseText = computed(() => ({
  connecting: '正在连接执行日志',
  waiting: '等待执行输出',
  streaming: '日志同步中',
  retrying: '日志连接暂时中断，正在重试',
  empty: '本次执行暂无日志输出',
  complete: '日志已加载完成',
})[logPhase.value])
const logPhaseBusy = computed(() => logPhase.value === 'connecting' || logPhase.value === 'retrying')
// 客户端合并流总行数上限（超出从最早段头部丢弃，防止长任务撑爆内存）
const LOG_CAP = 20000
// 拉取在飞标志：轮询 tick 遇上一轮未完成时跳过，避免慢网下请求堆叠
let logFetching = false
// 单飞被拦时登记重跑：终态补拉不能被静默吞掉（定时器已停，无下轮兜底）
let refetchQueued = false
// 离开页面中断拉取循环标志
let abortFetch = false
// 步骤终态集合（与后端步骤状态机一致）
const STEP_FINAL = ['success', 'failed', 'timeout', 'terminated']

/** pending/skipped 步骤不产生日志文件，不建段 */
function hasLog(status: string): boolean {
  return !['pending', 'skipped'].includes(status)
}

/** 为步骤补建日志段（幂等；按 step_order 保序，支持运行中动态发现新步骤） */
function ensureSegment(step: { step_order: number; step_name: string }): LogSegment {
  let seg = segments.value.find((s) => s.step_order === step.step_order)
  if (!seg) {
    seg = {
      step_order: step.step_order, step_name: step.step_name,
      lines: [], offset: 0, eof: false, done: false, truncated: false, dropped: 0,
    }
    segments.value.push(seg)
    segments.value.sort((a, b) => a.step_order - b.step_order)
    // 必须取回响应式代理返回：直接返回原始对象会绕过 Vue 代理，
    // 后续 lines.push 不触发重渲染（实测表现为尾段日志冷加载不显示）
    seg = segments.value.find((s) => s.step_order === step.step_order)!
  }
  return seg
}

/** 单段增量拉取直至 eof（每请求 2000 行；失败保留已有内容，下轮重试） */
async function fetchSegment(seg: LogSegment): Promise<boolean> {
  try {
    while (!abortFetch) {
      const data = await execApi.getExecutionLogs(executionId, {
        step_order: seg.step_order, offset: seg.offset, limit: 2000,
      })
      if (data.lines.length) {
        seg.lines.push(...data.lines.map(tokenizeLogLine))
        trimToCapacity()
        scrollLogToBottom()
      }
      seg.offset = data.next_offset
      seg.eof = data.eof
      if (data.eof) break
    }
    return true
  } catch {
    // 本轮未确认读到文件尾：重置 eof，禁止上层用陈旧 eof 置 done 而永久跳过收尾日志
    seg.eof = false
    return false
  }
}

/** 合并流行数上限裁剪：从最早段头部丢弃并标记 truncated */
function trimToCapacity() {
  let overflow = segments.value.reduce((n, s) => n + s.lines.length, 0) - LOG_CAP
  for (const seg of segments.value) {
    if (overflow <= 0) break
    const drop = Math.min(overflow, seg.lines.length)
    if (drop > 0) {
      seg.lines.splice(0, drop)
      seg.dropped += drop
      seg.truncated = true
      overflow -= drop
    }
  }
}

/** 拉取一轮：按步骤顺序对所有"已开跑且未 done"的段拉增量并推进 done 标记 */
async function fetchAllSegments(): Promise<void> {
  if (!detail.value) return
  if (logFetching) {
    refetchQueued = true
    return
  }
  logFetching = true
  let roundFailed = false
  try {
    for (const step of detail.value.steps) {
      if (abortFetch) return
      if (!hasLog(step.status)) continue
      const seg = ensureSegment(step)
      if (seg.done) continue
      // 拉取前捕获终态判定：保证 done 仅在"终态之后完整读到文件尾"时置位，
      // 避免 await 期间状态被事件推进导致用旧 eof 误判截尾
      const wasFinal = STEP_FINAL.includes(step.status)
      const fetched = await fetchSegment(seg)
      if (!fetched) roundFailed = true
      if (wasFinal && seg.eof) seg.done = true
    }
  } finally {
    if (!abortFetch) {
      logReady.value = true
      logFetchError.value = roundFailed
    }
    logFetching = false
    // 在飞期间有补拉请求被拦：立即重跑一轮，保证终态收尾日志不丢
    if (refetchQueued) {
      refetchQueued = false
      void fetchAllSegments()
    }
  }
}

// ---------- 底部跟随与步骤定位 ----------
// 仅当用户停留在底部附近(<40px)时新日志才自动滚底；上翻阅读/定位后不打扰
let nearBottom = true

function onLogScroll() {
  const el = logBox.value
  if (!el) return
  nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

function scrollLogToBottom() {
  if (!nearBottom) return
  requestAnimationFrame(() => {
    if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
  })
}

/** 点击步骤节点：日志框容器内定位到该步骤分隔行（尚无日志段则无操作） */
function locateStep(order: number) {
  const box = logBox.value
  const el = box?.querySelector<HTMLElement>(`[data-step="${order}"]`)
  if (!box || !el) return
  nearBottom = false // 定位到非末段时暂停跟底（定位点距底 <40px 时 scroll 事件会重新恢复跟底）
  box.scrollTop = el.offsetTop
}

// ---------- 日志框高度：默认铺满视口 + 底缘拖拽自定义（不持久化，刷新恢复默认） ----------
const logHeight = ref(440)
const MIN_LOG_HEIGHT = 240
// 用户拖拽过则窗口 resize 不再重算默认高度
let userResized = false

/** 默认高度 = 视口高 − 日志框顶部位置 − 留白(手柄+边框+页面底距) */
function fitLogHeight() {
  if (userResized || !logBox.value) return
  const top = logBox.value.getBoundingClientRect().top
  logHeight.value = Math.max(MIN_LOG_HEIGHT, Math.floor(window.innerHeight - top - 28))
}

// 当前活跃拖拽的清理函数：mouseup 丢失或组件卸载时兜底调用，避免全局监听与 userSelect 残留
let stopResize: (() => void) | null = null

/** 底缘手柄拖拽：mousedown 后跟踪全局 mousemove 调整高度，mouseup 收尾 */
function onResizeStart(e: MouseEvent) {
  if (e.button !== 0) return // 仅左键触发拖拽
  stopResize?.() // 防御：清掉可能残留的上一轮拖拽，避免覆盖 stopResize 泄漏旧监听
  e.preventDefault()
  userResized = true
  const startY = e.clientY
  const startH = logHeight.value
  // 极小窗口下保证上限不低于下限，避免 min/max 钳制顺序击穿 240 下限
  const maxH = Math.max(MIN_LOG_HEIGHT, window.innerHeight - 120)
  document.body.style.userSelect = 'none' // 拖拽期间禁止选中文本
  const onMove = (ev: MouseEvent) => {
    // mouseup 在窗口外丢失（拖出底缘松手/切窗）：检测到按键已抬起立即收尾自愈
    if (ev.buttons === 0) return stopResize?.()
    logHeight.value = Math.min(maxH, Math.max(MIN_LOG_HEIGHT, startH + ev.clientY - startY))
  }
  const onUp = () => stopResize?.()
  stopResize = () => {
    stopResize = null
    document.body.style.userSelect = ''
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

// ---------- 长轮询实时通道（事件长轮询 + 日志定时增量） ----------
let pollAbort = false // 事件长轮询循环停止标志
let polling = false // 事件轮询单飞：避免叠加多个并发循环占满连接
let logTimer: number | null = null // 日志增量拉取定时器（2s）
const pollActive = ref(false) // 实时通道指示灯：事件轮询循环在跑即为活跃
let lastSeq = 0
// 日志轮询间隔：2s 拉一次增量，观感接近 Jenkins 控制台实时输出
const LOG_POLL_INTERVAL = 2000

/** 应用状态事件（/events 长轮询返回的增量事件） */
function applyEvent(kind: string, data: Record<string, unknown>) {
  if (!detail.value) return
  if (kind === 'execution_status') {
    detail.value.status = data.status as execApi.ExecutionStatus
    if (data.ticket_status) detail.value.ticket_status = data.ticket_status as string
    if (isFinished.value) {
      void fetchAllSegments() // 终态前最后一段日志可能未到轮询 tick，补拉一次
      teardownRealtime()
    }
  } else if (kind === 'step_status') {
    const step = detail.value.steps.find((s) => s.step_order === data.step_order)
    if (step) {
      step.status = data.status as string
      step.exit_code = (data.exit_code as number | null) ?? step.exit_code
      step.error_summary = (data.error_summary as string | null) ?? step.error_summary
      step.started_at = (data.started_at as string | null) ?? step.started_at
      step.finished_at = (data.finished_at as string | null) ?? step.finished_at
      // 步骤终态：补拉该段收尾日志（fetchAllSegments 内部会推进 done 标记）
      if (STEP_FINAL.includes(step.status)) void fetchAllSegments()
    }
  }
}

/** 事件主通道：/events 长轮询循环（服务端挂起 30s，有事件立即返回） */
async function startEventPoll() {
  if (polling) return // 已有循环在跑：不再叠加
  polling = true
  pollAbort = false
  pollActive.value = true
  try {
    while (!pollAbort && !isFinished.value) {
      try {
        const data = await execApi.pollExecutionEvents(executionId, lastSeq)
        if (pollAbort) return
        lastSeq = Math.max(lastSeq, data.last_seq)
        for (const evt of data.events) applyEvent(evt.kind, evt.data)
        if (data.finished) {
          await loadDetail() // 终态兜底对齐（可能错过 step 事件）
          void fetchAllSegments() // 补拉收尾日志后结束
          return
        }
      } catch {
        await new Promise((r) => setTimeout(r, 3000)) // 网络异常退避
      }
    }
  } finally {
    polling = false
    pollActive.value = false
  }
}

/** 日志轮询 tick：对未完成段拉增量（在飞/已终态则跳过） */
function tickLogPoll() {
  if (logFetching || isFinished.value) return
  void fetchAllSegments()
}

/** 启动全部实时轮询（事件长轮询 + 日志定时增量） */
function startRealtime() {
  void startEventPoll()
  if (!logTimer) logTimer = window.setInterval(tickLogPoll, LOG_POLL_INTERVAL)
}

/** 终态/离开页面：停止全部轮询 */
function teardownRealtime() {
  pollAbort = true
  if (logTimer) {
    clearInterval(logTimer)
    logTimer = null
  }
}

// ---------- 执行控制（04 §6：仅创建人或 admin，权限 execution:control） ----------
const isAdmin = computed(() =>
  (userStore.userInfo?.roles ?? []).some((r) => r.code === 'admin'),
)
const canControl = computed(() => {
  if (!userStore.hasPerm('execution:control') || !detail.value) return false
  return isAdmin.value || detail.value.creator_id === userStore.userInfo?.id
})

/** 各控制操作允许的工单状态（与后端状态机一致，仅控制按钮显隐） */
const controlAllowed: Record<execApi.ControlOp, string[]> = {
  abort: ['queued', 'running', 'paused'],
  pause: ['running'],
  resume: ['paused'],
  'force-abort': ['running', 'paused'],
}

function showControl(op: execApi.ControlOp): boolean {
  if (op === 'force-abort' && !userStore.hasPerm('execution:force_control')) return false
  return canControl.value && controlAllowed[op].includes(detail.value?.ticket_status ?? '')
}

const controlling = ref(false)

async function onControl(op: execApi.ControlOp) {
  if (!detail.value) return
  controlling.value = true
  try {
    await execApi.controlTicket(detail.value.ticket_id, op)
    message.success('控制信号已下发，状态将随执行推进更新')
    await loadDetail() // 信号不直接改状态，刷新以拿到最新工单态
  } catch {
    /* 40901/40302 等提示由拦截器统一弹出 */
  } finally {
    controlling.value = false
  }
}

onMounted(async () => {
  // 先注册 resize 监听：若加载期间用户离开，卸载时的 remove 才必然配对（fitLogHeight 自带空值守卫）
  window.addEventListener('resize', fitLogHeight)
  requestAnimationFrame(fitLogHeight)
  await loadDetail()
  // 详情渲染完成后再测量日志框顶部位置计算默认高度
  requestAnimationFrame(fitLogHeight)
  if (!isFinished.value) startRealtime()
})

onBeforeUnmount(() => {
  // 离开页面：中断拉取循环（终态 teardown 不置此标志，保证终态补拉能跑完）
  abortFetch = true
  teardownRealtime()
  window.removeEventListener('resize', fitLogHeight)
  stopResize?.() // 拖拽中途离开页面：兜底清理全局监听与 userSelect
})
</script>

<template>
  <div class="execution-detail">
    <div v-if="detailLoading && !detail" class="detail-loading-spin" role="status" aria-label="正在加载执行详情">
      <a-spin />
    </div>

    <!-- 首屏骨架：延迟显示避免快速请求闪屏，同时占住最终布局高度 -->
    <div
      v-if="!detail"
      class="detail-loading-shell"
      :class="{ 'is-visible': showSkeleton }"
      aria-busy="true"
      aria-label="正在加载执行详情"
    >
      <div class="op-hero op-hero--indigo detail-hero detail-skeleton-hero">
        <div class="detail-skeleton-icon"><ThunderboltOutlined /></div>
        <div class="hero-main detail-skeleton-main">
          <div class="detail-skeleton-row">
            <span class="detail-skeleton-block detail-skeleton-title" />
            <span class="detail-skeleton-block detail-skeleton-pill" />
          </div>
          <span class="detail-skeleton-block detail-skeleton-sub" />
        </div>
        <div class="hero-actions detail-skeleton-actions">
          <span class="detail-skeleton-block detail-skeleton-button" />
          <span class="detail-skeleton-block detail-skeleton-button detail-skeleton-button-short" />
        </div>
      </div>

      <div class="step-flow detail-skeleton-flow" aria-hidden="true">
        <span class="detail-skeleton-block detail-skeleton-step" />
        <span class="detail-skeleton-block detail-skeleton-step" />
        <span class="detail-skeleton-block detail-skeleton-step" />
      </div>

      <div class="log-panel detail-skeleton-log">
        <div class="log-head">
          <span>执行日志（全部步骤）</span>
          <span class="log-phase"><LoadingOutlined spin />正在加载执行概览</span>
        </div>
        <div
          ref="logBox"
          class="log-body detail-skeleton-log-body"
          :style="{ height: logHeight + 'px' }"
          aria-hidden="true"
        >
          <div class="detail-skeleton-lines">
            <span class="detail-skeleton-block" />
            <span class="detail-skeleton-block" />
            <span class="detail-skeleton-block" />
            <span class="detail-skeleton-block" />
          </div>
        </div>
        <div class="log-resize"><span class="grip" /></div>
      </div>
    </div>

    <template v-else>
    <!-- 头部：返回 + 概要 + 控制按钮 -->
    <div class="op-hero op-hero--indigo detail-hero">
      <div class="op-hero-icon"><ThunderboltOutlined /></div>
      <div class="hero-main">
        <div class="op-hero-title">
          {{ detail?.ticket_no || `执行 #${executionId}` }}
          <a-tag
            v-if="detail"
            class="status-tag"
            :color="execStatusMeta[detail.status]?.color"
          >
            {{ execStatusMeta[detail.status]?.text || detail.status }}
          </a-tag>
          <a-tag v-if="detail?.interrupt_reason" class="status-tag interrupt-status-tag" color="warning">
            {{ interruptReasonText[detail.interrupt_reason] || detail.interrupt_reason }}
          </a-tag>
          <span class="ws-dot" :class="{ on: pollActive }" :title="pollActive ? '实时轮询中' : '实时轮询已停止（执行已结束）'" />
        </div>
        <div class="op-hero-sub">
          {{ detail?.title }}（作业主机：{{ detail?.job_host_name || '—' }}）
          · 开始 {{ fmtTime(detail?.started_at) }} · 结束 {{ fmtTime(detail?.finished_at) }}
        </div>
      </div>
      <div class="op-hero-extra hero-actions">
        <a-button class="back-btn" @click="router.push({ name: 'execution-list' })">
          <ArrowLeftOutlined />返回列表
        </a-button>
        <a-popconfirm v-if="showControl('pause')" placement="bottom" overlay-class-name="execution-control-popconfirm" title="确认暂停执行？在跑步骤将先跑完。" @confirm="onControl('pause')">
          <a-button :loading="controlling" class="op-btn-orange"><PauseCircleOutlined />暂停</a-button>
        </a-popconfirm>
        <a-popconfirm v-if="showControl('resume')" placement="bottom" overlay-class-name="execution-control-popconfirm" title="确认恢复执行？" @confirm="onControl('resume')">
          <a-button :loading="controlling" class="op-btn-green"><PlayCircleOutlined />恢复</a-button>
        </a-popconfirm>
        <a-popconfirm v-if="showControl('abort')" placement="bottom" overlay-class-name="execution-control-popconfirm" title="确认终止？未派发步骤将置为跳过。" @confirm="onControl('abort')">
          <a-button :loading="controlling" danger><StopOutlined />终止</a-button>
        </a-popconfirm>
        <a-popconfirm
          v-if="showControl('force-abort')"
          placement="bottomRight"
          overlay-class-name="execution-control-popconfirm"
          title="强制终止将强杀在跑 SSH 会话，可能造成作业主机业务中断，确认继续？"
          ok-text="强制终止"
          ok-type="danger"
          @confirm="onControl('force-abort')"
        >
          <a-button :loading="controlling" type="primary" danger><ThunderboltOutlined />强制终止</a-button>
        </a-popconfirm>
      </div>
    </div>

    <!-- 步骤条：状态节点 + 连接线，点击定位到合并流中对应日志段 -->
    <div class="step-flow">
      <template v-for="(s, i) in detail?.steps ?? []" :key="s.step_order">
        <a-tooltip :title="s.error_summary || undefined">
          <div
            class="step-node"
            :class="`is-${s.status}`"
            @click="locateStep(s.step_order)"
          >
            <span class="node-dot">
              <CheckOutlined v-if="s.status === 'success'" />
              <CloseOutlined v-else-if="s.status === 'failed' || s.status === 'timeout'" />
              <LoadingOutlined v-else-if="s.status === 'running'" spin />
              <PauseOutlined v-else-if="s.status === 'skipped'" />
              <StopOutlined v-else-if="s.status === 'terminated'" />
              <template v-else>{{ s.step_order }}</template>
            </span>
            <span class="node-body">
              <span class="node-title">{{ s.step_order }}. {{ s.step_name }}</span>
              <span class="node-meta">
                <b class="node-status">{{ stepStatusMeta[s.status]?.text || s.status }}</b>
                <span>耗时 {{ fmtDuration(s.started_at, s.finished_at) }}</span>
                <span v-if="s.exit_code !== null && s.exit_code !== 0" class="bad">退出码 {{ s.exit_code }}</span>
              </span>
            </span>
          </div>
        </a-tooltip>
        <span
          v-if="i < (detail?.steps.length ?? 0) - 1"
          class="step-conn"
          :class="{ done: s.status === 'success' }"
        />
      </template>
    </div>

    <!-- 日志显示屏：全部步骤合并流（分隔行区分步骤），历史 REST + 定时增量轮询 -->
    <div class="log-panel">
      <div class="log-head">
        <span>执行日志（全部步骤）</span>
        <span class="log-phase" :class="`is-${logPhase}`">
          <LoadingOutlined v-if="logPhaseBusy" spin />
          <span v-else class="log-phase-dot" />
          {{ logPhaseText }}
        </span>
      </div>
      <div
        ref="logBox"
        class="log-body"
        :style="{ height: logHeight + 'px' }"
        @scroll="onLogScroll"
      >
        <div v-if="logPhase === 'connecting'" class="log-state">
          <LoadingOutlined spin />
          <span>正在连接执行日志</span>
        </div>
        <div v-else-if="logPhase === 'waiting'" class="log-state">
          <span class="log-state-dot" />
          <span>等待执行输出</span>
        </div>
        <div v-else-if="logPhase === 'retrying' && !hasLogLines" class="log-state log-state-error">
          <LoadingOutlined spin />
          <span>日志连接暂时中断，正在重试</span>
        </div>
        <div v-else-if="logPhase === 'empty'" class="log-state">
          <span>本次执行暂无日志输出</span>
        </div>
        <template v-else>
        <div v-if="logPhase === 'retrying'" class="log-retry-banner">
          <LoadingOutlined spin />日志连接暂时中断，正在重试，已加载日志仍保留
        </div>
        <template v-for="seg in segments" :key="seg.step_order">
          <div class="log-sep" :data-step="seg.step_order">
            ━━━ 步骤 {{ seg.step_order }} · {{ seg.step_name }} ━━━
          </div>
          <div v-if="seg.truncated" class="log-trunc">…较早日志已省略…</div>
          <div v-for="(line, i) in seg.lines" :key="seg.dropped + i" class="log-line">
            <span v-for="(token, tokenIndex) in line" :key="tokenIndex" class="log-token" :class="`is-${token.kind}`">
              {{ token.text }}
            </span>
          </div>
        </template>
        <div v-if="logPhase === 'streaming'" class="log-tail-loading" aria-label="日志同步中"><span /><span /><span /></div>
        </template>
      </div>
      <div class="log-resize" title="拖拽调整日志区高度" @mousedown="onResizeStart">
        <span class="grip" />
      </div>
    </div>
    </template>
  </div>
</template>

<style scoped>
.execution-detail {
  position: relative;
}
.detail-loading-spin {
  position: absolute;
  z-index: 2;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--bg-card) 58%, transparent);
  pointer-events: none;
}
.detail-loading-shell {
  opacity: 0;
  transition: opacity 0.16s ease;
}
.detail-loading-shell.is-visible {
  opacity: 1;
}
.detail-skeleton-hero {
  min-height: 104px;
  background: var(--bg-card);
  box-shadow: var(--shadow-card);
}
.detail-skeleton-icon {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 9px;
  color: var(--text-3);
  background: var(--bg-hover);
  flex: none;
}
.detail-skeleton-main {
  flex: 1;
}
.detail-skeleton-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.detail-skeleton-block {
  display: block;
  border-radius: 5px;
  background: linear-gradient(90deg, var(--bg-hover), var(--bg-card), var(--bg-hover));
  background-size: 220% 100%;
  animation: detail-skeleton-shimmer 1.7s ease-in-out infinite;
}
.detail-skeleton-title {
  width: 184px;
  height: 23px;
}
.detail-skeleton-pill {
  width: 70px;
  height: 22px;
  border-radius: 999px;
}
.detail-skeleton-sub {
  width: min(520px, 72%);
  height: 13px;
  margin-top: 8px;
}
.detail-skeleton-actions {
  min-width: 164px;
}
.detail-skeleton-button {
  width: 76px;
  height: 31px;
}
.detail-skeleton-button-short {
  width: 62px;
}
.detail-skeleton-flow {
  min-height: 92px;
  gap: 10px;
}
.detail-skeleton-step {
  height: 48px;
  flex: 1;
}
.detail-skeleton-log-body {
  height: 440px;
  display: flex;
  align-items: flex-start;
}
.detail-skeleton-lines {
  width: min(560px, 78%);
  display: grid;
  gap: 12px;
}
.detail-skeleton-lines .detail-skeleton-block {
  height: 10px;
  background: linear-gradient(90deg, rgba(148, 163, 184, 0.08), rgba(125, 211, 252, 0.2), rgba(148, 163, 184, 0.08));
  background-size: 220% 100%;
}
.detail-skeleton-lines .detail-skeleton-block:nth-child(2) { width: 84%; }
.detail-skeleton-lines .detail-skeleton-block:nth-child(3) { width: 68%; }
.detail-skeleton-lines .detail-skeleton-block:nth-child(4) { width: 48%; }
@keyframes detail-skeleton-shimmer {
  0% { background-position: 150% 0; }
  100% { background-position: -50% 0; }
}
.detail-hero {
  align-items: center;
}
.hero-main {
  min-width: 0;
}
.status-tag {
  margin-left: 8px;
  vertical-align: 2px;
}
.hero-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
/* 实时通道指示灯 */
.ws-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #d9d9d9;
  margin-left: 8px;
  vertical-align: 2px;
}
.ws-dot.on {
  background: #52c41a;
  box-shadow: 0 0 4px #52c41a;
}

/* 步骤条：状态节点 + 连接线（风格对齐 op-card） */
.step-flow {
  display: flex;
  align-items: center;
  margin-bottom: 14px;
  padding: 10px 16px;
  background: var(--bg-card);
  border-radius: var(--radius-card);
  box-shadow: var(--shadow-card);
  overflow-x: auto;
}
.step-node {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 10px;
  cursor: pointer;
  flex: none;
  transition: background-color 0.2s, box-shadow 0.2s;
}
.step-node:hover {
  background: var(--bg-hover);
}
/* 状态圆节点：待执行显序号，其余状态显图标（色彩与状态标签一致） */
.node-dot {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  flex: none;
  border: 2px solid var(--border);
  color: var(--text-3);
  transition: all 0.2s;
}
.step-node.is-running .node-dot {
  border-color: transparent;
  color: #fff;
  background: var(--grad-blue);
  animation: node-pulse 1.6s ease-out infinite;
}
.step-node.is-success .node-dot {
  border-color: transparent;
  color: #fff;
  background: var(--grad-green);
}
.step-node.is-failed .node-dot,
.step-node.is-timeout .node-dot {
  border-color: transparent;
  color: #fff;
  background: var(--grad-red);
}
.step-node.is-skipped .node-dot,
.step-node.is-terminated .node-dot {
  border-color: transparent;
  color: #fff;
  background: var(--grad-orange);
}
@keyframes node-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.45);
  }
  100% {
    box-shadow: 0 0 0 8px rgba(59, 130, 246, 0);
  }
}
@media (prefers-reduced-motion: reduce) {
  .detail-loading-shell,
  .detail-skeleton-block {
    transition: none;
    animation: none;
  }
  .step-node.is-running .node-dot {
    animation: none;
  }
}
.node-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.node-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--text-1);
  white-space: nowrap;
}
.node-meta {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  color: var(--text-3);
  white-space: nowrap;
}
.node-status {
  font-weight: 600;
}
.step-node.is-running .node-status {
  color: var(--primary);
}
.step-node.is-success .node-status {
  color: var(--success);
}
.step-node.is-failed .node-status,
.step-node.is-timeout .node-status {
  color: var(--error);
}
.step-node.is-skipped .node-status,
.step-node.is-terminated .node-status {
  color: var(--warning);
}
.node-meta .bad {
  color: var(--error);
}
/* 步骤间连接线：前序步骤成功后点亮 */
.step-conn {
  flex: 1 1 40px;
  min-width: 24px;
  height: 2px;
  border-radius: 1px;
  background: var(--border);
  transition: background-color 0.3s;
}
.step-conn.done {
  background: var(--success);
}

/* 终端风格日志显示屏（全宽） */
.log-panel {
  border: 1px solid var(--border-1, #e5e7eb);
  border-radius: 8px;
  overflow: hidden;
}
.log-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: var(--bg-1, #fff);
  border-bottom: 1px solid var(--border-1, #e5e7eb);
  font-size: 13px;
}
.log-phase {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-3);
  font-size: 12px;
  white-space: nowrap;
}
.log-phase.is-streaming .log-phase-dot,
.log-phase.is-complete .log-phase-dot {
  background: var(--success);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--success) 16%, transparent);
}
.log-phase.is-waiting .log-phase-dot,
.log-phase.is-empty .log-phase-dot {
  background: var(--text-3);
}
.log-phase.is-retrying {
  color: var(--error);
}
.log-phase-dot,
.log-state-dot {
  width: 7px;
  height: 7px;
  display: inline-block;
  border-radius: 50%;
  background: var(--primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary) 14%, transparent);
}
.log-body {
  overflow: auto;
  background: #0f172a;
  color: #d1fae5;
  font-family: 'Cascadia Code', Consolas, Menlo, monospace;
  font-size: 12px;
  padding: 10px 12px;
  position: relative;
}
.log-state {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #8fa2b8;
  text-align: center;
}
.log-state .anticon {
  color: #7dd3fc;
  font-size: 18px;
}
.log-state-error {
  color: #fda4af;
}
.log-state-error .anticon {
  color: #fb7185;
}
.log-retry-banner {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: -2px 0 8px;
  padding: 5px 8px;
  border-left: 2px solid #fb7185;
  color: #fda4af;
  background: rgba(251, 113, 133, 0.08);
  font-family: 'Segoe UI', sans-serif;
  font-size: 11px;
}
.log-line {
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.6;
}
.log-token.is-timestamp { color: #64748b; }
.log-token.is-info, .log-token.is-path { color: #67e8f9; }
.log-token.is-success { color: #86efac; }
.log-token.is-warning { color: #fcd34d; }
.log-token.is-error { color: #fda4af; }
.log-token.is-command { color: #d8b4fe; }
.log-tail-loading {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 18px;
  padding-left: 2px;
}
.log-tail-loading span {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #7dd3fc;
  animation: log-tail-pulse 1.1s ease-in-out infinite;
}
.log-tail-loading span:nth-child(2) { animation-delay: 0.15s; }
.log-tail-loading span:nth-child(3) { animation-delay: 0.3s; }
@keyframes log-tail-pulse {
  0%, 100% { opacity: 0.25; transform: translateY(0); }
  50% { opacity: 1; transform: translateY(-2px); }
}
@media (prefers-reduced-motion: reduce) {
  .log-tail-loading span { animation: none; opacity: 0.7; }
}
/* 步骤分隔行：Jenkins 风格高亮标记，与普通日志行明显区分 */
.log-sep {
  color: #7dd3fc;
  font-weight: 700;
  margin: 8px 0 2px;
  user-select: none;
}
.log-sep:first-child {
  margin-top: 0;
}
/* 行数上限裁剪提示 */
.log-trunc {
  color: #64748b;
  font-style: italic;
}
/* 底缘拖拽手柄：ns-resize 光标 + 居中握纹 */
.log-resize {
  height: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: ns-resize;
  background: var(--bg-1, #fff);
  border-top: 1px solid var(--border-1, #e5e7eb);
}
.log-resize:hover .grip {
  background: var(--primary, #6366f1);
}
.log-resize .grip {
  width: 36px;
  height: 3px;
  border-radius: 2px;
  background: var(--border, #d1d5db);
  transition: background-color 0.2s;
}
.log-empty {
  color: #64748b;
  text-align: center;
  padding-top: 40px;
}
</style>
