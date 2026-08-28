<script setup lang="ts">
// 流程模板编辑器：只维护可复用步骤、审批和执行策略。
import { reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { DeleteOutlined, DownOutlined, PlusOutlined, UpOutlined } from '@ant-design/icons-vue'
import * as api from '@/api/job'
import { defaultExecStrategy, type ExecStrategy } from '@/api/ticket'
import { listRoleOptions } from '@/api/system'
import CodeEditor from '@/components/CodeEditor.vue'

const props = defineProps<{ open: boolean; processId: number | null; copyFromId?: number | null }>()
const emit = defineEmits<{ 'update:open': [boolean]; saved: [] }>()
const activeTab = ref('base')
const loading = ref(false)
const saving = ref(false)
const roles = ref<{ id: number; name: string }[]>([])
const stepActive = ref<number[]>([0])
const form = reactive<{ name: string; description: string; exec_strategy: ExecStrategy; steps: api.ProcessStep[] }>({
  name: '', description: '', exec_strategy: defaultExecStrategy(), steps: [],
})
const roleOptions = () => roles.value.map((r) => ({ label: r.name, value: r.id }))
function emptyStep(): api.ProcessStep { return { name: '', script_type: 'shell', content: '', timeout: 600, approval_role_id: null } }
function reset() { Object.assign(form, { name: '', description: '', exec_strategy: defaultExecStrategy(), steps: [emptyStep()] }); activeTab.value = 'base'; stepActive.value = [0] }
watch(() => [props.open, props.processId, props.copyFromId], async ([open]) => {
  if (!open) return
  reset(); roles.value = (await listRoleOptions()).items
  const sourceId = props.processId ?? props.copyFromId
  if (!sourceId) return
  loading.value = true
  try {
    const data = await api.getProcessTemplate(sourceId)
    Object.assign(form, { name: props.copyFromId && !props.processId ? `${data.name}-副本` : data.name, description: data.description || '', exec_strategy: { ...defaultExecStrategy(), ...data.exec_strategy }, steps: (data.steps || []).map((s) => ({ name: s.name, script_type: s.script_type, content: s.content, timeout: s.timeout, approval_role_id: s.approval_role_id ?? null })) })
    stepActive.value = form.steps.map((_, index) => index)
  } finally { loading.value = false }
}, { immediate: true })
function addStep() { form.steps.push(emptyStep()); stepActive.value.push(form.steps.length - 1) }
function removeStep(index: number) { if (form.steps.length <= 1) return; form.steps.splice(index, 1); stepActive.value = form.steps.map((_, i) => i) }
function moveStep(index: number, direction: -1 | 1) { const next = index + direction; if (next < 0 || next >= form.steps.length) return; [form.steps[index], form.steps[next]] = [form.steps[next], form.steps[index]] }
function validate(): string | null {
  if (!form.name.trim()) return '请填写流程模板名称'
  if (!form.steps.length) return '至少配置一个执行步骤'
  if (form.steps.some((s) => !s.name.trim() || !s.content.trim())) return '请填写每个步骤的名称和脚本内容'
  return null
}
async function save() {
  const error = validate(); if (error) return message.warning(error)
  saving.value = true
  try {
    const payload = { ...form, description: form.description || undefined }
    if (props.processId) await api.updateProcessTemplate(props.processId, payload)
    else await api.createProcessTemplate(payload)
    message.success('流程模板已保存'); emit('update:open', false); emit('saved')
  } finally { saving.value = false }
}
</script>

<template>
  <a-modal :open="open" :title="processId ? '编辑流程模板' : copyFromId ? '复制流程模板' : '新建流程模板'" :confirm-loading="saving" :width="960" @ok="save" @update:open="(value: boolean) => emit('update:open', value)">
    <a-spin :spinning="loading">
      <a-tabs v-model:active-key="activeTab">
        <a-tab-pane key="base" tab="基础信息"><a-form layout="vertical"><a-form-item label="流程名称" required><a-input v-model:value="form.name" placeholder="例如：应用发布流程" /></a-form-item><a-form-item label="说明"><a-textarea v-model:value="form.description" :rows="3" /></a-form-item></a-form></a-tab-pane>
        <a-tab-pane key="steps" :tab="`步骤编排（${form.steps.length}）`"><a-collapse v-model:active-key="stepActive"><a-collapse-panel v-for="(step, index) in form.steps" :key="index"><template #header><a-tag color="cyan">步骤 {{ index + 1 }}</a-tag>{{ step.name || '（未命名）' }}</template><template #extra><a-space @click.stop><a-button size="small" :disabled="index === 0" @click="moveStep(index, -1)"><UpOutlined /></a-button><a-button size="small" :disabled="index === form.steps.length - 1" @click="moveStep(index, 1)"><DownOutlined /></a-button><a-button size="small" danger :disabled="form.steps.length <= 1" @click="removeStep(index)"><DeleteOutlined /></a-button></a-space></template><a-form layout="vertical"><div class="form-row"><a-form-item label="步骤名称" required class="form-col"><a-input v-model:value="step.name" /></a-form-item><a-form-item label="脚本类型" class="form-col-sm"><a-select v-model:value="step.script_type" :options="[{ label: 'Shell', value: 'shell' }, { label: 'Playbook', value: 'playbook' }]" /></a-form-item><a-form-item label="超时（秒）" class="form-col-sm"><a-input-number v-model:value="step.timeout" :min="1" :max="86400" class="full-w" /></a-form-item></div><a-form-item label="脚本内容" required><CodeEditor v-model="step.content" :lang="step.script_type === 'playbook' ? 'yaml' : 'shell'" height="220px" /></a-form-item><a-form-item label="步骤前审批角色"><a-select v-model:value="step.approval_role_id" allow-clear placeholder="无需审批" :options="roleOptions()" /><div class="label-tip">该角色任意一名成员审批通过即可继续执行；驳回将直接终止工单。</div></a-form-item></a-form></a-collapse-panel></a-collapse><a-button type="dashed" block class="add-step-btn" @click="addStep"><PlusOutlined />添加步骤</a-button></a-tab-pane>
        <a-tab-pane key="strategy" tab="执行策略"><a-form layout="vertical"><div class="section-title">执行策略</div><div class="form-row"><a-form-item label="总超时（秒）" class="form-col-sm"><a-input-number v-model:value="form.exec_strategy.timeout" :min="1" :max="86400" class="full-w" /></a-form-item><a-form-item label=" " class="form-col checks"><a-checkbox v-model:checked="form.exec_strategy.fail_fast">失败即停</a-checkbox><a-checkbox v-model:checked="form.exec_strategy.kill_on_stop">终止时停止进程</a-checkbox></a-form-item></div><a-alert type="info" show-icon message="执行策略与步骤编排一同保存，并在创建工单时写入流程快照。" /></a-form></a-tab-pane>
      </a-tabs>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.form-row { display: flex; gap: 16px; }.form-col { flex: 1; }.form-col-sm { width: 150px; }.full-w { width: 100%; }.checks { display: flex; align-items: flex-end; gap: 16px; }.label-tip { font-size: 12px; color: var(--text-3); margin-top: 6px; }.add-step-btn { margin-top: 12px; }.section-title { font-weight: 600; margin: 4px 0 12px; }
@media (max-width: 760px) { .form-row { flex-direction: column; gap: 0; }.form-col-sm { width: 100%; } }
</style>
