<script setup lang="ts">
import type { TaskView } from '@/api/database'
import { statusNames, timeText } from './runtime'

// 历史默认保留停止依据，工作台可以仅显示执行状态与必要提示。
withDefaults(defineProps<{
  task: TaskView | null
  notice?: string
  showStopEvidence?: boolean
}>(), { showStopEvidence: true })
</script>
<template>
  <a-alert v-if="notice" :message="notice" type="warning" show-icon />
  <div v-if="task" class="db-task-state" aria-live="polite">
    <a-space wrap>
      <a-tag
        :color="task.status === 'succeeded' ? 'success' : ['queued','running'].includes(task.status) ? 'processing' : ['stopping','recovering'].includes(task.status) ? 'warning' : 'default'">{{ task.kind==='probe'&&task.status==='succeeded'?'测试通过':task.kind==='probe'&&task.status==='failed'?'测试失败':statusNames[task.status] }}</a-tag>
      <span>任务 #{{ task.task_id }}</span>
      <span>{{ task.operation_summary }}</span>
      <span>提交 {{ timeText(task.created_at) }}</span>
    </a-space>
    <a-alert v-if="['stopping','recovering'].includes(task.status)" message="已申请停止，目标停止状态尚未确认；不会自动重新执行。"
      type="warning" show-icon />
    <a-alert v-if="task.error_message" :message="task.error_message" type="error" show-icon />
    <div v-if="task.executed_statement" class="db-sql-summary">
      <b>实际执行</b>
      <pre>{{ task.executed_statement }}</pre>
    </div>
    <div v-if="showStopEvidence&&task.stop_confirmation">
      <span class="db-muted">{{ task.stop_confirmation.confirmed === true ? '停止已确认' : '停止待确认' }}</span>
      <details :key="task.task_id">
        <summary>查看停止依据</summary>
        <pre>{{ JSON.stringify(task.stop_confirmation, null, 2) }}</pre>
      </details>
    </div>
  </div>
</template>
