<script setup lang="ts">
// 工单模板编辑器：维护工单入口、参数契约和通知范围，流程步骤从流程模板引用。
import { computed, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue'
import * as api from '@/api/job'
import { listJobHosts, type JobHost } from '@/api/jobHost'
import { listRoleOptions } from '@/api/system'
import { receiverOptions, typeOptions } from './meta'
import CodeEditor from '@/components/CodeEditor.vue'
import { useUserStore } from '@/stores/user'

const props = defineProps<{
  open: boolean
  templateId: number | null
  copyFromId?: number | null
}>()
const emit = defineEmits<{ 'update:open': [boolean]; saved: [] }>()
const userStore = useUserStore()
const canReadSecret = userStore.hasPerm('secret:read')

type HostOption = Pick<JobHost, 'id' | 'name' | 'enabled'>
const loading = ref(false)
const saving = ref(false)
const activeTab = ref('base')
const hosts = ref<HostOption[]>([])
const processes = ref<api.ProcessTemplateItem[]>([])
const roles = ref<{ id: number; name: string }[]>([])
const scriptCredentials = ref<api.CredentialItem[]>([])
const form = reactive<api.TicketTemplateForm>(emptyForm())
const generatorScript = computed({ get: () => form.generator_script || '', set: (value: string) => (form.generator_script = value) })

function emptyForm(): api.TicketTemplateForm {
  return {
    name: '', type: 'daily_ops', description: '', job_host_id: undefined as unknown as number,
    process_template_id: undefined as unknown as number, params_schema: [], generator_script: '', generator_timeout: 60, allow_withdraw: true,
    credential_refs: [], notify_rules: [], visible_role_ids: [], status: 'enabled',
  }
}

function emptyParam(): api.TicketParam { return { name: '', label: '', source: 'user', input_type: 'text', options: [], default: '', required: false, description: '' } }

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
const scriptCredentialOptions = computed(() => scriptCredentials.value.map((credential) => ({
  label: `${credential.name}（${credential.auth_type === 'api_token' ? 'API Token' : credential.auth_type === 'username_password' ? '用户名密码' : '文本密钥文件'}）`,
  value: credential.id,
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
    if (canReadSecret) {
      const [tokens, accounts, files] = await Promise.all([
        api.listCredentials({ page: 1, page_size: 100, auth_type: 'api_token' }),
        api.listCredentials({ page: 1, page_size: 100, auth_type: 'username_password' }),
        api.listCredentials({ page: 1, page_size: 100, auth_type: 'secret_file' }),
      ])
      scriptCredentials.value = [...tokens.items, ...accounts.items, ...files.items]
    }

    const sourceId = props.templateId ?? props.copyFromId
    if (!sourceId) return
    const data = await api.getTemplate(sourceId)
    Object.assign(form, {
      name: props.copyFromId && !props.templateId ? `${data.name}-副本` : data.name,
      type: data.type,
      description: data.description || '',
      job_host_id: data.job_host_id,
      process_template_id: data.process_template_id,
      params_schema: (data.params_schema || []).map((p) => ({ ...emptyParam(), ...p, options: [...(p.options || [])] })),
      generator_script: data.generator_script || '',
      generator_timeout: data.generator_timeout || 60,
      credential_refs: (data.credential_refs || []).map((ref) => ({ alias: ref.alias, credential_id: ref.credential_id, credential_name: ref.credential_name })),
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

function addParam(): void { form.params_schema.push(emptyParam()) }
function removeParam(index: number): void { form.params_schema.splice(index, 1) }
function addOption(param: api.TicketParam): void { param.options.push('') }
function removeOption(param: api.TicketParam, index: number): void { param.options.splice(index, 1) }
function addCredentialRef(): void { form.credential_refs.push({ alias: '', credential_id: undefined as unknown as number }) }
function removeCredentialRef(index: number): void { form.credential_refs.splice(index, 1) }

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
  const names = new Set<string>()
  for (const param of form.params_schema) {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(param.name)) return `参数名“${param.name || '空'}”不合法`
    if (names.has(param.name)) return `参数名“${param.name}”重复`
    names.add(param.name)
    if (param.source === 'fixed' && !param.default) return `固定值参数“${param.name}”必须填写默认值`
    if (param.input_type === 'enum' && param.options.filter(Boolean).length === 0 && param.source !== 'generated') return `枚举参数“${param.name}”至少配置一个选项`
    param.options = param.options.filter(Boolean)
  }
  if (form.generator_script && !form.params_schema.some((p) => p.source === 'generated')) return '配置生成脚本时至少需要一个动态参数'
  if (form.params_schema.some((p) => p.source === 'generated') && !form.generator_script) return '配置动态参数时必须填写生成脚本'
  const aliases = new Set<string>()
  const credentialIds = new Set<number>()
  for (const ref of form.credential_refs) {
    if (!/^[A-Z][A-Z0-9_]{0,31}$/.test(ref.alias)) return '脚本密钥别名仅支持大写字母、数字和下划线'
    if (!ref.credential_id) return '请选择脚本密钥凭据'
    if (aliases.has(ref.alias)) return `脚本密钥别名“${ref.alias}”重复`
    if (credentialIds.has(ref.credential_id)) return '同一脚本密钥不能重复绑定'
    aliases.add(ref.alias)
    credentialIds.add(ref.credential_id)
  }
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
    const payload = { ...form, description: form.description || undefined, generator_script: form.generator_script || undefined, credential_refs: form.credential_refs.map((ref) => ({ alias: ref.alias, credential_id: ref.credential_id })), params_schema: form.params_schema.map((p) => ({ ...p, label: p.label || undefined, default: p.default || undefined, description: p.description || undefined })) }
    if (props.templateId) await api.updateTemplate(props.templateId, payload)
    else await api.createTemplate(payload)
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
            <template v-if="canReadSecret">
              <a-divider orientation="left" :orientation-margin="0">脚本密钥引用</a-divider>
              <div v-for="(ref, index) in form.credential_refs" :key="index" class="secret-ref-row">
                <a-input v-model:value="ref.alias" placeholder="别名，如 DEPLOY_TOKEN" />
                <a-select v-model:value="ref.credential_id" show-search option-filter-prop="label" :options="scriptCredentialOptions" placeholder="选择脚本密钥" />
                <a-button size="small" danger aria-label="删除脚本密钥引用" @click="removeCredentialRef(index)"><DeleteOutlined /></a-button>
              </div>
              <a-button size="small" class="op-btn-green" @click="addCredentialRef"><PlusOutlined />添加脚本密钥</a-button>
            </template>
            <a-form-item><a-checkbox v-model:checked="form.allow_withdraw">允许创建人终止待处理工单</a-checkbox></a-form-item>
          </a-form>
        </a-tab-pane>
        <a-tab-pane key="params" :tab="`参数与动态生成（${form.params_schema.length}）`">
          <a-alert type="info" show-icon message="参数配置属于当前工单模板；同一流程模板的其他工单入口可以使用不同的参数和动态生成脚本。" class="param-alert" />
          <div v-for="(param, index) in form.params_schema" :key="index" class="param-block">
            <div class="param-row"><a-input v-model:value="param.name" class="param-name" placeholder="参数名 *" /><a-input v-model:value="param.label" class="param-label" placeholder="显示名称" /><a-select v-model:value="param.source" class="param-source" :options="[{ label: '固定值', value: 'fixed' }, { label: '用户输入/选择', value: 'user' }, { label: '动态生成', value: 'generated' }]" /><a-select v-model:value="param.input_type" class="param-type" :options="[{ label: '文本', value: 'text' }, { label: '枚举单选', value: 'enum' }]" /><a-checkbox v-model:checked="param.required">必填</a-checkbox><a-button size="small" danger @click="removeParam(index)"><DeleteOutlined /></a-button></div>
            <div class="param-row param-detail"><a-input v-model:value="param.default" class="param-default" placeholder="默认值" /><a-input v-model:value="param.description" class="param-desc" placeholder="参数说明" /><a-button v-if="param.input_type === 'enum'" size="small" class="op-btn-green" @click="addOption(param)"><PlusOutlined />添加选项</a-button></div>
            <div v-if="param.input_type === 'enum'" class="option-list"><a-tag v-for="(_, optionIndex) in param.options" :key="optionIndex" closable @close="removeOption(param, optionIndex)"><a-input v-model:value="param.options[optionIndex]" size="small" placeholder="枚举值" /></a-tag></div>
          </div>
          <a-button size="small" class="op-btn-green" @click="addParam"><PlusOutlined />添加参数</a-button>
          <a-divider orientation="left">动态参数生成脚本（可选）</a-divider>
          <div class="form-row"><a-form-item label="脚本超时（秒）" class="form-col-sm"><a-input-number v-model:value="form.generator_timeout" :min="1" :max="3600" class="full-w" /></a-form-item></div>
          <CodeEditor v-model="generatorScript" lang="shell" height="180px" />
          <div class="label-tip">脚本在创建工单快照前于作业主机执行；返回固定值时无需用户选择，返回列表时提供单选。</div>
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
.form-row { display: flex; gap: 16px; }.form-col { flex: 1; }.form-col-sm { width: 150px; }.full-w { width: 100%; }.section-title { font-weight: 600; margin: 4px 0 12px; }.label-tip { font-size: 12px; color: var(--text-3); margin-top: 6px; }.param-alert { margin-bottom: 14px; }.param-block { padding: 14px 0 12px; border-bottom: 1px solid var(--border); }.param-row { display: grid; grid-template-columns: 150px 150px 160px 120px minmax(56px, auto) 36px; align-items: center; gap: 8px; margin-bottom: 8px; }.param-row > * { min-width: 0; width: 100%; }.param-detail { grid-template-columns: minmax(340px, 1fr) minmax(180px, 260px) auto; }.param-detail .op-btn-green { width: auto; white-space: nowrap; justify-self: start; }.option-list { display: flex; flex-wrap: wrap; gap: 6px; }.option-list :deep(.ant-tag) { display: inline-flex; align-items: center; gap: 4px; padding: 3px 6px; }.option-list :deep(.ant-input) { width: 120px; }.secret-ref-row { display: grid; grid-template-columns: minmax(180px, 0.7fr) minmax(260px, 1fr) 36px; align-items: center; gap: 8px; margin-bottom: 8px; }.secret-ref-row > * { min-width: 0; width: 100%; }.secret-ref-row .ant-btn { width: 36px; }.notify-row { display: grid; grid-template-columns: 150px minmax(260px, 1fr) 150px 36px; align-items: center; gap: 8px; margin-bottom: 8px; }.notify-row > * { min-width: 0; width: 100%; }.notify-row .ant-btn { width: 36px; }
@media (max-width: 760px) { .form-row { flex-direction: column; gap: 0; }.form-col-sm { width: 100%; }.param-row { grid-template-columns: minmax(120px, 1fr) minmax(120px, 1fr) 36px; }.param-row > .param-source, .param-row > .param-type, .param-row > .ant-checkbox-wrapper { grid-column: span 1; }.param-detail { grid-template-columns: minmax(220px, 1fr) minmax(160px, 0.75fr) auto; }.notify-row { grid-template-columns: 1fr 36px; }.notify-row .rule-event, .notify-row .rule-receivers, .notify-row .rule-channels { grid-column: 1; }.notify-row .ant-btn { grid-column: 2; grid-row: 1 / span 3; } }
</style>
