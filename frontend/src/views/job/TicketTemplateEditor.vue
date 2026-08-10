<script setup lang="ts">
// 工单模板编辑器：只维护工单入口和通知范围，流程步骤与参数统一从流程模板引用。
import { computed, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue'
import * as api from '@/api/job'
import { listJobHosts, type JobHost } from '@/api/jobHost'
import { listRoleOptions } from '@/api/system'
import { receiverOptions, typeOptions } from './meta'

const props = defineProps<{
  open: boolean
  templateId: number | null
  copyFromId?: number | null
}>()
const emit = defineEmits<{ 'update:open': [boolean]; saved: [] }>()

type HostOption = Pick<JobHost, 'id' | 'name' | 'enabled'>
const loading = ref(false)
const saving = ref(false)
const activeTab = ref('base')
const hosts = ref<HostOption[]>([])
const processes = ref<api.ProcessTemplateItem[]>([])
const roles = ref<{ id: number; name: string }[]>([])
const form = reactive<api.TicketTemplateForm>(emptyForm())

function emptyForm(): api.TicketTemplateForm {
  return {
    name: '', type: 'daily_ops', description: '', job_host_id: undefined as unknown as number,
    process_template_id: undefined as unknown as number, allow_withdraw: true,
    notify_rules: [], visible_role_ids: [], status: 'enabled',
  }
}

const roleOptions = computed(() => roles.value.map((role) => ({ label: role.name, value: role.id })))
const receiverSelectOptions = computed(() => receiverOptions(roles.value))
const hostSelectOptions = computed(() => hosts.value.map((host) => ({
  label: `${host.name}${host.enabled ? '' : '（已停用）'}`,
  value: host.id,
  disabled: !host.enabled && host.id !== form.job_host_id,
})))
const processSelectOptions = computed(() => processes.value.map((process) => ({
  label: `${process.name}${process.status === 'disabled' ? '（已停用）' : ''}`,
  value: process.id,
  disabled: process.status === 'disabled' && process.id !== form.process_template_id,
})))

function resetForm(): void {
  Object.assign(form, emptyForm())
  activeTab.value = 'base'
}

/** 加载编辑器依赖和源模板，复制模式只读取源数据并以新模板保存。 */
async function load(): Promise<void> {
  resetForm()
  loading.value = true
  try {
    const [hostData, processData, roleData] = await Promise.all([
      listJobHosts({ page: 1, page_size: 100 }),
      api.listProcessTemplates({ page: 1, page_size: 100 }),
      listRoleOptions(),
    ])
    hosts.value = hostData.items.map((host) => ({ id: host.id, name: host.name, enabled: host.enabled }))
    processes.value = processData.items
    roles.value = roleData.items

    const sourceId = props.templateId ?? props.copyFromId
    if (!sourceId) return
    const data = await api.getTemplate(sourceId)
    Object.assign(form, {
      name: props.copyFromId && !props.templateId ? `${data.name}-副本` : data.name,
      type: data.type,
      description: data.description || '',
      job_host_id: data.job_host_id,
      process_template_id: data.process_template_id,
      allow_withdraw: data.allow_withdraw,
      notify_rules: (data.notify_rules || []).map((rule) => ({
        event: rule.event, receivers: [...rule.receivers], channels: [...rule.channels],
      })),
      visible_role_ids: [...(data.visible_role_ids || [])],
      status: props.copyFromId && !props.templateId ? 'enabled' : data.status,
    })
  } finally {
    loading.value = false
  }
}

watch(() => [props.open, props.templateId, props.copyFromId], ([open]) => {
  if (open) void load()
}, { immediate: true })

function addNotify(): void {
  form.notify_rules.push({ event: 'ticket.pending_approval', receivers: ['creator'], channels: ['email'] })
}

function removeNotify(index: number): void {
  form.notify_rules.splice(index, 1)
}

function validate(): string | null {
  if (!form.name.trim()) return '请填写工单模板名称'
  if (!form.job_host_id) return '请选择作业主机'
  if (!form.process_template_id) return '请选择流程模板'
  if (props.copyFromId && !props.templateId) {
    const process = processes.value.find((item) => item.id === form.process_template_id)
    if (process?.status === 'disabled') return '停用流程模板不能用于复制创建工单模板'
  }
  return null
}

/** 保存工单模板；复制模式不携带源 ID，始终走新建接口。 */
async function save(): Promise<void> {
  const error = validate()
  if (error) return message.warning(error)
  saving.value = true
  try {
    if (props.templateId) await api.updateTemplate(props.templateId, form)
    else await api.createTemplate(form)
    message.success('工单模板已保存')
    emit('update:open', false)
    emit('saved')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <a-modal
    :open="open"
    :title="templateId ? '编辑工单模板' : copyFromId ? '复制工单模板' : '新建工单模板'"
    :confirm-loading="saving"
    :width="960"
    @ok="save"
    @update:open="(value: boolean) => emit('update:open', value)"
  >
    <a-spin :spinning="loading">
      <a-tabs v-model:active-key="activeTab">
        <a-tab-pane key="base" tab="基础信息">
          <a-form layout="vertical">
            <div class="form-row">
              <a-form-item label="模板名称" required class="form-col"><a-input v-model:value="form.name" placeholder="例如：应用发布工单" /></a-form-item>
              <a-form-item label="类型" required class="form-col-sm"><a-select v-model:value="form.type" :options="typeOptions" /></a-form-item>
            </div>
            <div class="form-row">
              <a-form-item label="作业主机" required class="form-col"><a-select v-model:value="form.job_host_id" show-search option-filter-prop="label" :options="hostSelectOptions" /></a-form-item>
              <a-form-item label="流程模板" required class="form-col"><a-select v-model:value="form.process_template_id" show-search option-filter-prop="label" :options="processSelectOptions" /></a-form-item>
            </div>
            <a-form-item label="说明"><a-textarea v-model:value="form.description" :rows="3" /></a-form-item>
            <a-form-item><a-checkbox v-model:checked="form.allow_withdraw">允许创建人终止待处理工单</a-checkbox></a-form-item>
          </a-form>
        </a-tab-pane>
        <a-tab-pane key="notify" tab="通知与可见范围">
          <a-form layout="vertical">
            <div class="section-title">通知规则</div>
            <div v-for="(rule, index) in form.notify_rules" :key="index" class="notify-row">
              <a-select v-model:value="rule.event" class="rule-event" :options="[{ label: '待审批', value: 'ticket.pending_approval' }, { label: '审批通过', value: 'ticket.approved' }, { label: '审批驳回', value: 'ticket.rejected' }]" />
              <a-select v-model:value="rule.receivers" mode="multiple" class="rule-receivers" :options="receiverSelectOptions" />
              <a-select v-model:value="rule.channels" mode="multiple" class="rule-channels" :options="[{ label: '站内信', value: 'inapp' }, { label: '邮件', value: 'email' }]" />
              <a-button size="small" danger aria-label="删除通知规则" @click="removeNotify(index)"><DeleteOutlined /></a-button>
            </div>
            <a-button size="small" class="op-btn-green" @click="addNotify"><PlusOutlined />添加通知规则</a-button>
            <a-divider />
            <div class="section-title">可见范围</div>
            <a-form-item label="可见角色"><a-select v-model:value="form.visible_role_ids" mode="multiple" :options="roleOptions" placeholder="留空表示全部角色可见" /></a-form-item>
            <div class="label-tip">只有选中角色的成员可以看到并创建该工单模板。</div>
          </a-form>
        </a-tab-pane>
      </a-tabs>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.form-row { display: flex; gap: 16px; }.form-col { flex: 1; }.form-col-sm { width: 150px; }.section-title { font-weight: 600; margin: 4px 0 12px; }.label-tip { font-size: 12px; color: var(--text-3); margin-top: 6px; }.notify-row { display: grid; grid-template-columns: 150px minmax(260px, 1fr) 150px 36px; align-items: center; gap: 8px; margin-bottom: 8px; }.notify-row > * { min-width: 0; width: 100%; }.notify-row .ant-btn { width: 36px; }
@media (max-width: 760px) { .form-row { flex-direction: column; gap: 0; }.form-col-sm { width: 100%; }.notify-row { grid-template-columns: 1fr 36px; }.notify-row .rule-event, .notify-row .rule-receivers, .notify-row .rule-channels { grid-column: 1; }.notify-row .ant-btn { grid-column: 2; grid-row: 1 / span 3; } }
</style>
