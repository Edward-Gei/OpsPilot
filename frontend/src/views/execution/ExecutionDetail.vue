<script setup lang="ts">
// 执行详情页（M5）：标题横幅 + 流水线流程图 + 日志显示屏（作业主机上串行执行）
// 实时通道（纯长轮询，WS 已弃用）：
//   状态事件 = /events 长轮询（服务端挂起 30s，有事件立即返回）
//   日志追加 = /logs 每 2s 按 offset 增量拉取（Jenkins 式尾随效果）
// 弃用 WS 原因：切换日志焦点后 WS 订阅失效导致日志无法实时更新，
// 长轮询无连接状态、无订阅概念，切步骤即重置偏移量拉取，天然免疫该类 bug
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
import { execStatusMeta, interruptReasonText, stepStatusMeta, triggeredByText } from './meta'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const executionId = Number(route.params.id)

// ---------- 详情状态 ----------
const detail = ref<execApi.ExecutionDetail | null>(null)
const loading = ref(false)

/** 终态判定：终态后关闭实时通道，不再重连 */
const isFinished = computed(() =>
  ['success', 'failed', 'terminated', 'interrupted'].includes(detail.value?.status ?? ''),
)

/** 初始加载 / 断线恢复：REST 拉全量详情（含步骤列表） */
async function loadDetail() {
  loading.value = true
  try {
    const d = await execApi.getExecution(executionId)
    detail.value = d
    void fetchAllSegments()
  } finally {
    loading.value = false
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
  lines: string[]
  offset: number // 该步骤下一次拉取的行偏移
  eof: boolean // 服务端已读到当前文件尾
  done: boolean // 步骤已终态且 eof：不再拉取
  truncated: boolean // 触发行数上限后段头部已丢弃较早日志
}
const segments = ref<LogSegment[]>([])
const logBox = ref<HTMLElement | null>(null)
// 日志框高度（Task 2 将扩展为视口自适应 + 拖拽；本任务先固定默认值）
const logHeight = ref(440)
// 客户端合并流总行数上限（超出从最早段头部丢弃，防止长任务撑爆内存）
const LOG_CAP = 20000
// 拉取在飞标志：轮询 tick 遇上一轮未完成时跳过，避免慢网下请求堆叠
let logFetching = false
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
      lines: [], offset: 0, eof: false, done: false, truncated: false,
    }
    segments.value.push(seg)
    segments.value.sort((a, b) => a.step_order - b.step_order)
  }
  return seg
}

/** 单段增量拉取直至 eof（每请求 2000 行；失败静默跳过，下轮重试） */
async function fetchSegment(seg: LogSegment): Promise<void> {
  try {
    while (!abortFetch) {
      const data = await execApi.getExecutionLogs(executionId, {
        step_order: seg.step_order, offset: seg.offset, limit: 2000,
      })
      if (data.lines.length) {
        seg.lines.push(...data.lines)
        trimToCapacity()
        scrollLogToBottom()
      }
      seg.offset = data.next_offset
      seg.eof = data.eof
      if (data.eof) break
    }
  } catch {
    /* 日志文件未生成等场景静默（该段本轮跳过） */
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
      seg.truncated = true
      overflow -= drop
    }
  }
}

/** 拉取一轮：按步骤顺序对所有"已开跑且未 done"的段拉增量并推进 done 标记 */
async function fetchAllSegments(): Promise<void> {
  if (logFetching || !detail.value) return
  logFetching = true
  try {
    for (const step of detail.value.steps) {
      if (abortFetch) return
      if (!hasLog(step.status)) continue
      const seg = ensureSegment(step)
      if (seg.done) continue
      await fetchSegment(seg)
      // 步骤已终态且拉到文件尾：该段完成，后续轮询不再触碰
      if (seg.eof && STEP_FINAL.includes(step.status)) seg.done = true
    }
  } finally {
    logFetching = false
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
  nearBottom = false // 定位阅读期间暂停自动跟底
  box.scrollTop = el.offsetTop
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
  abortFetch = true
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
  await loadDetail()
  if (!isFinished.value) startRealtime()
})

onBeforeUnmount(teardownRealtime)
</script>

<template>
  <div>
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
          <a-tag v-if="detail?.interrupt_reason" color="warning">
            {{ interruptReasonText[detail.interrupt_reason] || detail.interrupt_reason }}
          </a-tag>
          <span class="ws-dot" :class="{ on: pollActive }" :title="pollActive ? '实时轮询中' : '实时轮询已停止（执行已结束）'" />
        </div>
        <div class="op-hero-sub">
          {{ detail?.title }}（作业主机：{{ detail?.job_host_name || '—' }}）
          · {{ triggeredByText[detail?.triggered_by ?? ''] || detail?.triggered_by }}
          · 开始 {{ fmtTime(detail?.started_at) }} · 结束 {{ fmtTime(detail?.finished_at) }}
        </div>
      </div>
      <div class="op-hero-extra hero-actions">
        <a-button class="back-btn" @click="router.push({ name: 'execution-list' })">
          <ArrowLeftOutlined />返回列表
        </a-button>
        <a-popconfirm v-if="showControl('pause')" title="确认暂停执行？在跑步骤将先跑完。" @confirm="onControl('pause')">
          <a-button :loading="controlling" class="op-btn-orange"><PauseCircleOutlined />暂停</a-button>
        </a-popconfirm>
        <a-popconfirm v-if="showControl('resume')" title="确认恢复执行？" @confirm="onControl('resume')">
          <a-button :loading="controlling" class="op-btn-green"><PlayCircleOutlined />恢复</a-button>
        </a-popconfirm>
        <a-popconfirm v-if="showControl('abort')" title="确认中止？未派发步骤将置为跳过。" @confirm="onControl('abort')">
          <a-button :loading="controlling" danger><StopOutlined />中止</a-button>
        </a-popconfirm>
        <a-popconfirm
          v-if="showControl('force-abort')"
          title="强制中止将强杀在跑 SSH 会话，可能造成作业主机业务中断，确认继续？"
          ok-text="强制中止"
          ok-type="danger"
          @confirm="onControl('force-abort')"
        >
          <a-button :loading="controlling" type="primary" danger>强制中止</a-button>
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
      </div>
      <div
        ref="logBox"
        class="log-body"
        :style="{ height: logHeight + 'px' }"
        @scroll="onLogScroll"
      >
        <template v-for="seg in segments" :key="seg.step_order">
          <div class="log-sep" :data-step="seg.step_order">
            ━━━ 步骤 {{ seg.step_order }} · {{ seg.step_name }} ━━━
          </div>
          <div v-if="seg.truncated" class="log-trunc">…较早日志已省略…</div>
          <div v-for="(line, i) in seg.lines" :key="i" class="log-line">{{ line }}</div>
        </template>
        <div v-if="!segments.length" class="log-empty">暂无日志输出</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
.log-body {
  overflow: auto;
  background: #0f172a;
  color: #d1fae5;
  font-family: 'Cascadia Code', Consolas, Menlo, monospace;
  font-size: 12px;
  padding: 10px 12px;
  position: relative;
}
.log-line {
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.6;
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
.log-empty {
  color: #64748b;
  text-align: center;
  padding-top: 40px;
}
</style>
