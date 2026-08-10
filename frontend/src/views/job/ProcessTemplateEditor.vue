<script setup lang="ts">
// 流程模板编辑器：沿用原模板编辑器的分栏表单和步骤折叠布局，所有流程级配置集中保存。
import { computed, reactive, ref, watch } from 'vue'
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
type EditorForm = Omit<api.ProcessTemplateForm, 'generator_script'> & { generator_script: string }
const form = reactive<EditorForm>({ name: '', description: '', params_schema: [], generator_script: '', generator_timeout: 60, exec_strategy: defaultExecStrategy(), steps: [] })
const roleOptions = () => roles.value.map((r) => ({ label: r.name, value: r.id }))
const generatorScript = computed({ get: () => form.generator_script || '', set: (value: string) => (form.generator_script = value) })

function emptyParam(): api.ProcessParam { return { name: '', label: '', source: 'user', input_type: 'text', options: [], default: '', required: false, description: '' } }
function emptyStep(): api.ProcessStep { return { name: '', script_type: 'shell', content: '', timeout: 600, approval_role_id: null } }
function reset() {
  Object.assign(form, { name: '', description: '', params_schema: [], generator_script: '', generator_timeout: 60, exec_strategy: defaultExecStrategy(), steps: [emptyStep()] })
  activeTab.value = 'base'; stepActive.value = [0]
}
watch(() => [props.open, props.processId, props.copyFromId], async ([open]) => {
  if (!open) return
  reset(); roles.value = (await listRoleOptions()).items
  const sourceId = props.processId ?? props.copyFromId
  if (!sourceId) return
  loading.value = true
  try {
    const data = await api.getProcessTemplate(sourceId)
    Object.assign(form, { name: props.copyFromId && !props.processId ? `${data.name}-副本` : data.name, description: data.description || '', params_schema: (data.params_schema || []).map((p) => ({ ...emptyParam(), ...p, options: [...(p.options || [])] })), generator_script: data.generator_script || '', generator_timeout: data.generator_timeout || 60, exec_strategy: { ...defaultExecStrategy(), ...data.exec_strategy }, steps: (data.steps || []).map((s) => ({ name: s.name, script_type: s.script_type, content: s.content, timeout: s.timeout, approval_role_id: s.approval_role_id ?? null })) })
    stepActive.value = form.steps.map((_, index) => index)
  } finally { loading.value = false }
}, { immediate: true })
function addParam() { form.params_schema.push(emptyParam()) }
function removeParam(index: number) { form.params_schema.splice(index, 1) }
function addOption(param: api.ProcessParam) { param.options.push('') }
function removeOption(param: api.ProcessParam, index: number) { param.options.splice(index, 1) }
function addStep() { form.steps.push(emptyStep()); stepActive.value.push(form.steps.length - 1) }
function removeStep(index: number) { if (form.steps.length <= 1) return; form.steps.splice(index, 1); stepActive.value = form.steps.map((_, i) => i) }
function moveStep(index: number, direction: -1 | 1) { const next = index + direction; if (next < 0 || next >= form.steps.length) return; [form.steps[index], form.steps[next]] = [form.steps[next], form.steps[index]] }
function validate(): string | null {
  if (!form.name.trim()) return '请填写流程模板名称'
  if (!form.steps.length) return '至少配置一个执行步骤'
  if (form.steps.some((s) => !s.name.trim() || !s.content.trim())) return '请填写每个步骤的名称和脚本内容'
  const names = new Set<string>()
  for (const p of form.params_schema) {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(p.name)) return `参数名“${p.name || '空'}”不合法`
    if (names.has(p.name)) return `参数名“${p.name}”重复`
    names.add(p.name)
    if (p.source === 'fixed' && !p.default) return `固定值参数“${p.name}”必须填写默认值`
    if (p.input_type === 'enum' && p.options.filter(Boolean).length === 0 && p.source !== 'generated') return `枚举参数“${p.name}”至少配置一个选项`
    p.options = p.options.filter(Boolean)
  }
  if (form.generator_script && !form.params_schema.some((p) => p.source === 'generated')) return '配置生成脚本时至少需要一个动态参数'
  if (form.params_schema.some((p) => p.source === 'generated') && !form.generator_script) return '配置动态参数时必须填写生成脚本'
  return null
}
async function save() {
  const error = validate(); if (error) return message.warning(error)
  saving.value = true
  try {
    const payload = { ...form, description: form.description || undefined, generator_script: form.generator_script || undefined, params_schema: form.params_schema.map((p) => ({ ...p, label: p.label || undefined, default: p.default || undefined, description: p.description || undefined })) }
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
        <a-tab-pane key="base" tab="基础信息"><a-form layout="vertical"><div class="form-row"><a-form-item label="流程名称" required class="form-col"><a-input v-model:value="form.name" placeholder="例如：应用发布流程" /></a-form-item><a-form-item label="生成脚本超时（秒）" class="form-col-sm"><a-input-number v-model:value="form.generator_timeout" :min="1" :max="3600" class="full-w" /></a-form-item></div><a-form-item label="说明"><a-textarea v-model:value="form.description" :rows="3" /></a-form-item></a-form></a-tab-pane>
        <a-tab-pane key="params" :tab="`流程参数（${form.params_schema.length}）`"><a-alert type="info" show-icon message="固定值直接写入工单快照；用户参数在创建工单时输入或选择；动态参数由作业主机执行脚本后生成，并向创建人和审批人展示。" class="param-alert" /><div v-for="(param, index) in form.params_schema" :key="index" class="param-block"><div class="param-row"><a-input v-model:value="param.name" class="param-name" placeholder="参数名 *" /><a-input v-model:value="param.label" class="param-label" placeholder="显示名称" /><a-select v-model:value="param.source" class="param-source" :options="[{ label: '固定值', value: 'fixed' }, { label: '用户输入/选择', value: 'user' }, { label: '动态生成', value: 'generated' }]" /><a-select v-model:value="param.input_type" class="param-type" :options="[{ label: '文本', value: 'text' }, { label: '枚举单选', value: 'enum' }]" /><a-checkbox v-model:checked="param.required">必填</a-checkbox><a-button size="small" danger @click="removeParam(index)"><DeleteOutlined /></a-button></div><div class="param-row param-detail"><a-input v-model:value="param.default" class="param-default" placeholder="默认值" /><a-input v-model:value="param.description" class="param-desc" placeholder="参数说明" /><a-button v-if="param.input_type === 'enum'" size="small" class="op-btn-green" @click="addOption(param)"><PlusOutlined />添加选项</a-button></div><div v-if="param.input_type === 'enum'" class="option-list"><a-tag v-for="(_, optionIndex) in param.options" :key="optionIndex" closable @close="removeOption(param, optionIndex)"><a-input v-model:value="param.options[optionIndex]" size="small" placeholder="枚举值" /></a-tag></div></div><a-button size="small" class="op-btn-green" @click="addParam"><PlusOutlined />添加参数</a-button><a-divider orientation="left">动态参数生成脚本（可选）</a-divider><CodeEditor v-model="form.generator_script" lang="shell" height="180px" /><div class="label-tip">脚本在创建工单快照前于作业主机执行；返回固定值时无需用户选择，返回列表时提供单选。</div></a-tab-pane>
        <a-tab-pane key="steps" :tab="`步骤编排（${form.steps.length}）`"><a-collapse v-model:active-key="stepActive"><a-collapse-panel v-for="(step, index) in form.steps" :key="index"><template #header><a-tag color="cyan">步骤 {{ index + 1 }}</a-tag>{{ step.name || '（未命名）' }}</template><template #extra><a-space @click.stop><a-button size="small" :disabled="index === 0" @click="moveStep(index, -1)"><UpOutlined /></a-button><a-button size="small" :disabled="index === form.steps.length - 1" @click="moveStep(index, 1)"><DownOutlined /></a-button><a-button size="small" danger :disabled="form.steps.length <= 1" @click="removeStep(index)"><DeleteOutlined /></a-button></a-space></template><a-form layout="vertical"><div class="form-row"><a-form-item label="步骤名称" required class="form-col"><a-input v-model:value="step.name" /></a-form-item><a-form-item label="脚本类型" class="form-col-sm"><a-select v-model:value="step.script_type" :options="[{ label: 'Shell', value: 'shell' }, { label: 'Playbook', value: 'playbook' }]" /></a-form-item><a-form-item label="超时（秒）" class="form-col-sm"><a-input-number v-model:value="step.timeout" :min="1" :max="86400" class="full-w" /></a-form-item></div><a-form-item label="脚本内容" required><CodeEditor v-model="step.content" :lang="step.script_type === 'playbook' ? 'yaml' : 'shell'" height="220px" /></a-form-item><a-form-item label="步骤前审批角色"><a-select v-model:value="step.approval_role_id" allow-clear placeholder="无需审批" :options="roleOptions()" /><div class="label-tip">该角色任意一名成员审批通过即可继续执行；驳回将直接终止工单。</div></a-form-item></a-form></a-collapse-panel></a-collapse><a-button type="dashed" block class="add-step-btn" @click="addStep"><PlusOutlined />添加步骤</a-button></a-tab-pane>
        <a-tab-pane key="strategy" tab="执行策略"><a-form layout="vertical"><div class="section-title">执行策略</div><div class="form-row"><a-form-item label="总超时（秒）" class="form-col-sm"><a-input-number v-model:value="form.exec_strategy.timeout" :min="1" :max="86400" class="full-w" /></a-form-item><a-form-item label=" " class="form-col checks"><a-checkbox v-model:checked="form.exec_strategy.fail_fast">失败即停</a-checkbox><a-checkbox v-model:checked="form.exec_strategy.kill_on_stop">终止时停止进程</a-checkbox></a-form-item></div><a-alert type="info" show-icon message="执行策略与步骤编排一同保存，并在创建工单时写入流程快照。" /></a-form></a-tab-pane>
      </a-tabs>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.form-row { display: flex; gap: 16px; }.form-col { flex: 1; }.form-col-sm { width: 150px; }.full-w { width: 100%; }.checks { display: flex; align-items: flex-end; gap: 16px; }.label-tip { font-size: 12px; color: var(--text-3); margin-top: 6px; }.param-alert { margin-bottom: 14px; }.param-block { padding: 14px 0 12px; border-bottom: 1px solid var(--border); }.param-row { display: grid; grid-template-columns: 150px 150px 160px 120px minmax(56px, auto) 36px; align-items: center; gap: 8px; margin-bottom: 8px; }.param-row > * { min-width: 0; width: 100%; }.param-name { width: 100%; }.param-label { width: 100%; }.param-source { width: 100%; }.param-type { width: 100%; }.param-default { width: 100%; }.param-desc { width: 100%; }.param-detail { grid-template-columns: minmax(340px, 1fr) minmax(180px, 260px) auto; padding-left: 0; }.param-detail .op-btn-green { width: auto; white-space: nowrap; justify-self: start; }.option-list { display: flex; flex-wrap: wrap; gap: 6px; padding-left: 0; }.option-list :deep(.ant-tag) { display: inline-flex; align-items: center; gap: 4px; padding: 3px 6px; }.option-list :deep(.ant-input) { width: 120px; }.add-step-btn { margin-top: 12px; }.section-title { font-weight: 600; margin: 4px 0 12px; }
@media (max-width: 760px) { .param-row { grid-template-columns: minmax(120px, 1fr) minmax(120px, 1fr) 36px; }.param-row > .param-source, .param-row > .param-type { grid-column: span 1; }.param-row > .ant-checkbox-wrapper { grid-column: span 1; }.param-detail { grid-template-columns: minmax(220px, 1fr) minmax(160px, 0.75fr) auto; } }
</style>
