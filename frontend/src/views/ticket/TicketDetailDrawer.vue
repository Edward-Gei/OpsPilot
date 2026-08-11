<script setup lang="ts">
// 工单详情抽屉（V2）：基本信息 + 提交参数 + 步骤前审批时间线 + 作业主机/步骤快照 + 执行概要
// showApprove=true（待办审批页）时在审批中状态下展示 通过/驳回 操作区
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import * as ticketApi from '@/api/ticket'
import { useUserStore } from '@/stores/user'
import { execStatusMeta } from '@/views/execution/meta'
import { fmtTime, statusMeta } from './meta'

const props = defineProps<{
  open: boolean
  ticketId: number | null
  showApprove?: boolean
}>()
const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'changed'): void
}>()

const loading = ref(false)
const detail = ref<ticketApi.TicketDetail | null>(null)
const router = useRouter()
const userStore = useUserStore()

/** 拉取工单详情（打开抽屉或切换工单时刷新） */
async function load() {
  if (!props.ticketId) return
  loading.value = true
  try {
    detail.value = await ticketApi.getTicket(props.ticketId)
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.open, props.ticketId],
  () => {
    if (props.open && props.ticketId) load()
  },
)

const awaiting = computed(() => detail.value?.status === 'approving')

// 审批时间线：按步骤快照关联审批记录，避免依赖已废弃的节点模型。
interface TimelineRow {
  color: string
  title: string
  comment?: string
  time?: string
}
const timeline = computed<TimelineRow[]>(() => {
  const d = detail.value
  if (!d) return []
  return d.steps
    .filter((step) => step.approval_role_id)
    .map((step) => {
      const rec = d.approvals.find((a) => a.step_order === step.step_order)
      const roleName = step.approval_role_name || `角色 #${step.approval_role_id}`
      if (rec) {
        return {
          color: rec.action === 'approve' ? 'green' : 'red',
          title: `第 ${step.step_order} 步 · ${step.step_name}：${rec.action === 'approve' ? '通过' : '驳回'} · ${rec.approver_name}`,
          comment: rec.comment || undefined,
          time: fmtTime(rec.created_at),
        }
      }
      if (awaiting.value && step.step_order === d.current_step) {
        return { color: 'blue', title: `第 ${step.step_order} 步 · ${step.step_name}：等待「${roleName}」审批` }
      }
      return { color: 'gray', title: `第 ${step.step_order} 步 · ${step.step_name}：等待「${roleName}」审批` }
    })
})

/** 执行策略一行摘要（模板规则快照） */
const strategyText = computed(() => {
  const s = detail.value?.exec_strategy
  if (!s || s.timeout === undefined) return '—'
  return [
    `超时 ${s.timeout}s`,
    s.fail_fast ? '失败即停' : '失败继续',
  ].join(' · ')
})

/** 作业主机一行摘要（提交时固化快照） */
const jobHostText = computed(() => {
  const d = detail.value
  if (!d) return '—'
  if (d.job_host) return `${d.job_host.name}（${d.job_host.ip}）`
  return d.job_host_name || '—'
})

// ---------- 审批操作（待办页复用本抽屉） ----------
const comment = ref('')
const acting = ref('')

/** 审批通过/驳回：驳回意见必填（后端 40001 校验，前端同步拦截） */
async function onApprove(action: 'approve' | 'reject') {
  if (!detail.value) return
  if (action === 'reject' && !comment.value.trim()) {
    message.warning('驳回时必须填写审批意见')
    return
  }
  acting.value = action
  try {
    const res = await ticketApi.approveTicket(detail.value.id, action, comment.value || undefined)
    if (action === 'reject') message.success('已驳回，工单关闭')
    else message.success(res.status === 'queued' ? '已通过，工单进入执行队列' : '已通过，流转至下一审批步骤')
    comment.value = ''
    emit('changed')
    await load()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    acting.value = ''
  }
}
/** 跳转执行详情页（步骤列表 + 实时日志，需 execution:read） */
function openExecution() {
  if (!detail.value?.execution) return
  emit('update:open', false)
  router.push({ name: 'execution-detail', params: { id: detail.value.execution.id } })
}
</script>

<template>
  <a-drawer
    :open="open"
    :width="760"
    :title="detail ? `工单详情 · ${detail.ticket_no}` : '工单详情'"
    @close="emit('update:open', false)"
  >
    <a-spin :spinning="loading">
      <template v-if="detail">
        <!-- 头部：标题（=模板名快照）+ 状态 -->
        <div class="d-head">
          <b class="d-title">{{ detail.title }}</b>
          <a-tag :color="statusMeta[detail.status]?.color">
            {{ statusMeta[detail.status]?.text || detail.status }}
          </a-tag>
        </div>

        <a-descriptions bordered size="small" :column="{ xs: 1, sm: 2 }" class="d-desc op-desc-table">
          <a-descriptions-item label="作业主机">{{ jobHostText }}</a-descriptions-item>
          <a-descriptions-item label="提交人">{{ detail.creator_name }}</a-descriptions-item>
          <a-descriptions-item label="提交时间">{{ fmtTime(detail.submitted_at) }}</a-descriptions-item>
          <a-descriptions-item label="完成时间">{{ fmtTime(detail.finished_at) }}</a-descriptions-item>
          <a-descriptions-item label="执行策略" :span="2">{{ strategyText }}</a-descriptions-item>
          <a-descriptions-item v-if="Object.keys(detail.params).length" label="提交参数" :span="2">
            <div class="d-params">
              <a-tag v-for="(v, k) in detail.params" :key="k" color="geekblue">{{ k }} = {{ v }}</a-tag>
            </div>
          </a-descriptions-item>
          <a-descriptions-item v-if="detail.credential_refs?.length" label="引用凭据" :span="2">
            <div class="d-params">
              <a-tag v-for="r in detail.credential_refs" :key="r.alias" color="purple">
                {{ r.alias }} → {{ r.credential_name }}
              </a-tag>
            </div>
          </a-descriptions-item>
        </a-descriptions>

        <!-- 审批时间线（无步骤前审批的工单不展示） -->
        <template v-if="timeline.length">
          <div class="d-section">审批步骤</div>
          <a-timeline class="d-timeline">
            <a-timeline-item v-for="(row, i) in timeline" :key="i" :color="row.color">
              <div>{{ row.title }}</div>
              <div v-if="row.comment" class="d-tl-comment">意见：{{ row.comment }}</div>
              <div v-if="row.time" class="d-tl-time">{{ row.time }}</div>
            </a-timeline-item>
          </a-timeline>
        </template>

        <!-- 执行概要（M5：可跳执行详情页看步骤列表与实时日志） -->
        <template v-if="detail.execution">
          <div class="d-section">
            执行概要
            <a-button
              v-if="userStore.hasPerm('execution:read')"
              size="small"
              class="op-btn-cyan d-exec-btn"
              @click="openExecution"
            >查看执行详情</a-button>
          </div>
          <a-descriptions bordered size="small" :column="{ xs: 1, sm: 2 }" class="d-desc op-desc-table">
            <a-descriptions-item label="状态">
              <a-tag :color="execStatusMeta[detail.execution.status as keyof typeof execStatusMeta]?.color">
                {{ execStatusMeta[detail.execution.status as keyof typeof execStatusMeta]?.text || detail.execution.status }}
              </a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="步骤数">{{ detail.execution.total_steps }}</a-descriptions-item>
          </a-descriptions>
        </template>

        <!-- 步骤快照：提交时固化的脚本内容与生效参数（含 fixed 固定值） -->
        <div class="d-section">执行步骤（{{ detail.steps.length }}）</div>
        <a-collapse class="d-steps">
          <a-collapse-panel
            v-for="s in detail.steps"
            :key="s.step_order"
            :header="`第 ${s.step_order} 步 · ${s.step_name}（超时 ${s.timeout}s）`"
          >
            <div v-if="Object.keys(s.params).length" class="d-params">
              <a-tag v-for="(v, k) in s.params" :key="k" color="geekblue">{{ k }} = {{ v }}</a-tag>
            </div>
            <pre class="d-content">{{ s.content_snap }}</pre>
          </a-collapse-panel>
        </a-collapse>

        <!-- 审批操作区：仅待办页且工单处于审批中 -->
        <template v-if="showApprove && awaiting">
          <div class="d-section">审批操作</div>
          <a-textarea
            v-model:value="comment"
            :rows="2"
            placeholder="审批意见（驳回时必填）"
            :maxlength="512"
          />
          <div class="d-actions">
            <a-button type="primary" :loading="acting === 'approve'" @click="onApprove('approve')">
              通过
            </a-button>
            <a-button danger :loading="acting === 'reject'" @click="onApprove('reject')">驳回</a-button>
          </div>
        </template>
      </template>
    </a-spin>
  </a-drawer>
</template>

<style scoped>
.d-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}
.d-title {
  font-size: 16px;
  color: var(--text-1);
}
.d-desc {
  margin-bottom: 4px;
}
.d-section {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-1);
  margin: 18px 0 10px;
}
.d-exec-btn {
  margin-left: 10px;
  font-weight: 400;
}
.d-timeline {
  padding: 6px 4px 0;
}
.d-tl-comment {
  font-size: 12px;
  color: var(--text-2);
  margin-top: 2px;
}
.d-tl-time {
  font-size: 12px;
  color: var(--text-3);
  margin-top: 2px;
}
.d-params {
  margin-bottom: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.d-content {
  background: var(--bg-input);
  border-radius: 8px;
  padding: 10px 12px;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}
.d-actions {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}
</style>
