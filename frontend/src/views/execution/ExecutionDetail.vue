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
    if (activeStep.value === 0 && d.steps.length) selectStep(d.steps[0].step_order)
  } finally {
    loading.value = false
  }
}

// ---------- 流水线步骤节点（点击切换日志焦点） ----------
const activeStep = ref(0)

const activeStepInfo = computed(() =>
  detail.value?.steps.find((s) => s.step_order === activeStep.value) ?? null,
)

/** 切换日志焦点步骤：重置偏移量拉历史，后续增量由日志轮询定时器接管 */
function selectStep(order: number) {
  activeStep.value = order
  logLines.value = []
  logOffset.value = 0
  logEof.value = true
  void fetchHistoryLogs(true)
}

/** 步骤耗时：完成后按 started/finished 计算，未完成显示 — */
function fmtDuration(started: string | null, finished: string | null): string {
  if (!started || !finished) return '—'
  const ms = new Date(finished).getTime() - new Date(started).getTime()
  if (ms < 0) return '—'
  const s = Math.round(ms / 1000)
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}

// ---------- 日志面板（历史 REST + 定时增量拉取追加） ----------
const logLines = ref<string[]>([])
const logEof = ref(true)
const logOffset = ref(0)
const logBox = ref<HTMLElement | null>(null)
// 客户端日志行数上限（超出丢弃最早行，防止长任务撑爆内存）
const LOG_CAP = 5000
// 日志请求序号：快速切换步骤时丢弃过期响应，避免旧步骤日志覆盖当前面板
let logReqSeq = 0

// 日志请求在飞标志：轮询 tick 遇上一请求未完成时跳过，避免慢网下请求堆叠
let logFetching = false

/** 拉取日志：reset=true 整体替换（切步骤），false 从当前偏移续拉（轮询增量/加载更多） */
async function fetchHistoryLogs(reset: boolean) {
  if (!activeStep.value) return
  const seq = ++logReqSeq
  logFetching = true
  try {
    const data = await execApi.getExecutionLogs(executionId, {
      step_order: activeStep.value,
      offset: reset ? 0 : logOffset.value,
      limit: 2000,
    })
    if (seq !== logReqSeq) return // 焦点已切换：丢弃过期响应
    if (reset) {
      logLines.value = data.lines
      scrollLogToBottom()
    } else {
      appendLogLines(data.lines)
    }
    logOffset.value = data.next_offset
    logEof.value = data.eof
  } catch {
    /* 日志文件未生成等场景静默（面板显示空态） */
  } finally {
    logFetching = false
  }
}

/** 继续加载历史日志（超长日志分段拉取） */
function loadMoreLogs() {
  void fetchHistoryLogs(false)
}

function appendLogLines(lines: string[]) {
  if (!lines.length) return
  logLines.value.push(...lines)
  if (logLines.value.length > LOG_CAP) {
    logLines.value.splice(0, logLines.value.length - LOG_CAP)
  }
  scrollLogToBottom()
}

/** 底部跟随：新日志到达自动滚到底 */
function scrollLogToBottom() {
  requestAnimationFrame(() => {
    if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
  })
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
      void fetchHistoryLogs(false) // 终态前最后一段日志可能未到轮询 tick，补拉一次
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
          void fetchHistoryLogs(false) // 补拉收尾日志后结束
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

/** 日志轮询 tick：从当前偏移拉增量追加（在飞/无焦点/已终态则跳过） */
function tickLogPoll() {
  if (logFetching || !activeStep.value || isFinished.value) return
  void fetchHistoryLogs(false)
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

    <!-- 步骤条：状态节点 + 连接线，点击切换日志焦点 -->
    <div class="step-flow">
      <template v-for="(s, i) in detail?.steps ?? []" :key="s.step_order">
        <a-tooltip :title="s.error_summary || undefined">
          <div
            class="step-node"
            :class="[`is-${s.status}`, { active: s.step_order === activeStep }]"
            @click="selectStep(s.step_order)"
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

    <!-- 日志显示屏：全宽终端风格，历史 REST + 定时增量轮询追加 -->
    <div class="log-panel">
      <div class="log-head">
        <span>
          日志 · {{ activeStepInfo ? `步骤 ${activeStep} · ${activeStepInfo.step_name}` : '未选择步骤' }}
        </span>
        <a-button v-if="!logEof" size="small" @click="loadMoreLogs">加载更多</a-button>
      </div>
      <div ref="logBox" class="log-body">
        <div v-for="(line, i) in logLines" :key="i" class="log-line">{{ line }}</div>
        <div v-if="!logLines.length" class="log-empty">
          {{ activeStep ? '暂无日志输出' : '点击上方步骤节点查看日志' }}
        </div>
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
.step-node.active {
  background: rgba(99, 102, 241, 0.1);
  box-shadow: inset 0 0 0 1px rgba(99, 102, 241, 0.45);
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
  height: 440px;
  overflow: auto;
  background: #0f172a;
  color: #d1fae5;
  font-family: 'Cascadia Code', Consolas, Menlo, monospace;
  font-size: 12px;
  padding: 10px 12px;
}
.log-line {
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.6;
}
.log-empty {
  color: #64748b;
  text-align: center;
  padding-top: 180px;
}
</style>
