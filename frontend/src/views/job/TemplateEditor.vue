<script setup lang="ts">
// 模板编辑器：V2 全量规则配置（基础信息/步骤编排/执行策略/审批规则/通知规则/权限范围）
// 规则任一变更后端自动升版（TPL-06）；仅改名称/说明不升版
import { reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { DeleteOutlined, DownOutlined, PlusOutlined, UpOutlined } from '@ant-design/icons-vue'
import * as jobApi from '@/api/job'
import { defaultExecStrategy, type ExecStrategy } from '@/api/ticket'
import CodeEditor from '@/components/CodeEditor.vue'
import { approveModeOptions, channelOptions, editorLang, eventOptions, receiverOptions, scriptTypeOptions, typeOptions } from './meta'

const props = defineProps<{
  open: boolean
  /** null=新建；非空=编辑该模板（打开时拉详情回填） */
  templateId: number | null
  /** 复制源模板 id：templateId 为空时生效，预填该模板全部配置作为新建（名称自动加「-副本」） */
  copyFromId?: number | null
  apps: { id: number; name: string }[]
  roles: { id: number; name: string }[]
  credentials: { id: number; name: string }[]
}>()
const emit = defineEmits<{ 'update:open': [boolean]; saved: [] }>()

// ---------- 编辑态数据结构（输入框绑定用纯字符串，提交时转 null） ----------
interface EditParam {
  name: string
  label: string
  default: string
  required: boolean
  fixed: boolean
  description: string
}
interface EditStep {
  name: string
  script_type: 'shell' | 'playbook'
  content: string
  credential_id: number | undefined
  timeout: number
}
interface EditNode {
  role_id: number | undefined
  approve_mode: 'any' | 'all' | 'seq'
}
interface EditNotifyRule {
  event: string | undefined
  receivers: string[]
  channels: string[]
}

const activeTab = ref('base')
const loading = ref(false)
const form = reactive({
  name: '',
  type: 'ops' as jobApi.TemplateType,
  description: '',
  app_id: undefined as number | undefined,
  exec_strategy: defaultExecStrategy(),
  approval_enabled: false,
  allow_withdraw: true,
  allow_transfer: false,
  allow_countersign: false,
  visible_role_ids: [] as number[],
  changelog: '',
})
const steps = ref<EditStep[]>([])
// 全局参数定义：对所有步骤生效（保存时同一份写入每个步骤的 params_schema，
// 工单提交端已按 TPL-03 同名合并，后端/执行引擎无需改动）
const globalParams = ref<EditParam[]>([])
const nodes = ref<EditNode[]>([])
const notifyRules = ref<EditNotifyRule[]>([])
const stepActive = ref<number[]>([]) // 步骤折叠面板展开项

function emptyStep(): EditStep {
  return { name: '', script_type: 'shell', content: '', credential_id: undefined, timeout: 600 }
}

/** 各步骤存量 params_schema 合并为全局参数：与后端 TPL-03 规则一致
 * （同名首现定义为准；任一步骤必填即必填、任一步骤固定即固定），
 * 保证旧模板（步骤级参数）升级到全局参数后提交表单行为不变 */
function mergeStepParams(stepList: { params_schema: jobApi.TemplateParam[] }[]): EditParam[] {
  const merged = new Map<string, EditParam>()
  for (const s of stepList) {
    for (const p of s.params_schema || []) {
      const exist = merged.get(p.name)
      if (exist) {
        exist.required = exist.required || p.required
        exist.fixed = exist.fixed || p.fixed
      } else {
        merged.set(p.name, {
          name: p.name, label: p.label || '', default: p.default ?? '',
          required: p.required, fixed: p.fixed, description: p.description || '',
        })
      }
    }
  }
  return [...merged.values()]
}

/** 打开时初始化：新建给一个空步骤；编辑/复制拉源模板详情回填 */
watch(
  () => props.open,
  async (open) => {
    if (!open) return
    activeTab.value = 'base'
    Object.assign(form, {
      name: '', type: 'ops', description: '', app_id: undefined,
      exec_strategy: defaultExecStrategy(), approval_enabled: false,
      allow_withdraw: true, allow_transfer: false, allow_countersign: false,
      visible_role_ids: [], changelog: '',
    })
    steps.value = [emptyStep()]
    globalParams.value = []
    nodes.value = []
    notifyRules.value = []
    stepActive.value = [0]
    // 编辑回填自身；复制模式回填源模板（保存时仍走新建接口）
    const loadId = props.templateId ?? props.copyFromId ?? null
    if (loadId == null) return
    loading.value = true
    try {
      const d = await jobApi.getTemplate(loadId)
      Object.assign(form, {
        // 复制模式名称加后缀，避免与源模板重名混淆
        name: props.templateId == null ? `${d.name}-副本` : d.name,
        type: d.type,
        description: d.description || '',
        app_id: d.app_id,
        exec_strategy: { ...defaultExecStrategy(), ...d.exec_strategy },
        approval_enabled: d.approval_enabled,
        allow_withdraw: d.allow_withdraw,
        allow_transfer: d.allow_transfer,
        allow_countersign: d.allow_countersign,
        visible_role_ids: [...d.visible_role_ids],
      })
      steps.value = d.steps.map((s) => ({
        name: s.name,
        script_type: s.script_type,
        content: s.content,
        credential_id: s.credential_id,
        timeout: s.timeout,
      }))
      // 存量步骤级参数自动合并去重为全局参数
      globalParams.value = mergeStepParams(d.steps)
      nodes.value = d.approval_nodes.map((n) => ({ role_id: n.role_id, approve_mode: n.approve_mode }))
      notifyRules.value = d.notify_rules.map((r) => ({ event: r.event, receivers: [...r.receivers], channels: [...r.channels] }))
      stepActive.value = steps.value.map((_, i) => i)
    } finally {
      loading.value = false
    }
  },
)

// ---------- 步骤编排（顺序即执行顺序，支持上移/下移） ----------
function addStep() {
  steps.value.push(emptyStep())
  stepActive.value = [...stepActive.value, steps.value.length - 1]
}
function removeStep(i: number) {
  steps.value.splice(i, 1)
}
function moveStep(i: number, dir: -1 | 1) {
  const j = i + dir
  if (j < 0 || j >= steps.value.length) return
  ;[steps.value[i], steps.value[j]] = [steps.value[j], steps.value[i]]
}
function addParam() {
  globalParams.value.push({ name: '', label: '', default: '', required: false, fixed: false, description: '' })
}

// ---------- 审批节点 / 通知规则动态行 ----------
function addNode() {
  nodes.value.push({ role_id: undefined, approve_mode: 'any' })
}
function addNotifyRule() {
  notifyRules.value.push({ event: undefined, receivers: ['creator'], channels: ['email'] })
}

// ---------- 前端预校验（与后端规则一致，减少一次往返） ----------
function validate(): string | null {
  if (!form.name) return '请填写模板名称'
  if (!form.app_id) return '请选择目标应用'
  if (!steps.value.length) return '至少需要 1 个步骤'
  for (const [i, s] of steps.value.entries()) {
    const no = `步骤 ${i + 1}`
    if (!s.name) return `${no}：请填写步骤名`
    if (!s.content) return `${no}：请填写脚本内容`
    if (!s.credential_id) return `${no}：请选择执行凭据`
  }
  // 全局参数校验：命名合法、不重名、固定值必带默认值（与后端规则一致）
  const names = new Set<string>()
  for (const p of globalParams.value) {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(p.name)) return `参数名「${p.name || '(空)'}」不合法（字母/下划线开头）`
    if (names.has(p.name)) return `参数名「${p.name}」重复`
    names.add(p.name)
    if (p.fixed && !p.default) return `固定值参数「${p.name}」必须提供默认值`
  }
  if (form.approval_enabled) {
    if (!nodes.value.length) return '开启审批后至少需要 1 个审批节点'
    if (nodes.value.some((n) => !n.role_id)) return '每个审批节点必须选择角色'
  }
  for (const r of notifyRules.value) {
    if (!r.event) return '通知规则：请选择触发事件'
    if (!r.receivers.length || !r.channels.length) return '通知规则：收件人与渠道不能为空'
  }
  return null
}

const saving = ref(false)

/** 组装全量配置体提交；编辑响应带 version_bumped 提示是否升版 */
async function onSubmit() {
  const err = validate()
  if (err) {
    message.warning(err)
    return
  }
  // 全局参数统一序列化（空字符串转 null，与后端快照比较语义一致），同一份写入每个步骤
  const paramsSchema: jobApi.TemplateParam[] = globalParams.value.map((p) => ({
    name: p.name, label: p.label || null, default: p.default || null,
    required: p.required, fixed: p.fixed, description: p.description || null,
  }))
  const payload: jobApi.TemplateForm = {
    name: form.name,
    type: form.type,
    description: form.description || undefined,
    app_id: form.app_id!,
    steps: steps.value.map((s) => ({
      name: s.name,
      script_type: s.script_type,
      content: s.content,
      credential_id: s.credential_id!,
      timeout: s.timeout,
      params_schema: paramsSchema,
    })),
    exec_strategy: form.exec_strategy as ExecStrategy,
    approval_enabled: form.approval_enabled,
    approval_nodes: form.approval_enabled
      ? nodes.value.map((n, i) => ({ node_order: i + 1, role_id: n.role_id!, approve_mode: n.approve_mode }))
      : [],
    allow_withdraw: form.allow_withdraw,
    allow_transfer: form.allow_transfer,
    allow_countersign: form.allow_countersign,
    notify_rules: notifyRules.value.map((r) => ({ event: r.event!, receivers: r.receivers, channels: r.channels })),
    visible_role_ids: form.visible_role_ids,
    changelog: form.changelog || undefined,
  }
  saving.value = true
  try {
    if (props.templateId != null) {
      const res = await jobApi.updateTemplate(props.templateId, payload)
      message.success(res.version_bumped ? `已保存并生成新版本 v${res.current_version}` : '已保存（规则无变化，未升版）')
    } else {
      await jobApi.createTemplate(payload)
      message.success('模板已创建（v1）')
    }
    emit('update:open', false)
    emit('saved')
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <a-modal
    :open="open"
    :title="templateId != null ? '编辑模板' : copyFromId != null ? '复制模板' : '新建模板'"
    :confirm-loading="saving"
    :width="960"
    @ok="onSubmit"
    @update:open="(v: boolean) => emit('update:open', v)"
  >
    <a-spin :spinning="loading">
      <a-tabs v-model:active-key="activeTab">
        <!-- 基础信息 -->
        <a-tab-pane key="base" tab="基础信息">
          <a-form layout="vertical">
            <div class="form-row">
              <a-form-item label="模板名称" required class="form-col">
                <a-input v-model:value="form.name" placeholder="如 重启 Nginx" />
              </a-form-item>
              <a-form-item label="类型" required class="form-col-sm">
                <a-select v-model:value="form.type" :options="typeOptions" />
              </a-form-item>
              <a-form-item label="目标应用" required class="form-col">
                <a-select
                  v-model:value="form.app_id"
                  placeholder="工单执行的目标应用"
                  show-search
                  option-filter-prop="label"
                  :options="apps.map((a) => ({ label: a.name, value: a.id }))"
                />
              </a-form-item>
            </div>
            <a-form-item label="说明">
              <a-textarea v-model:value="form.description" :rows="2" placeholder="模板用途说明" />
            </a-form-item>
            <a-form-item v-if="templateId != null" label="版本说明">
              <a-input v-model:value="form.changelog" placeholder="本次变更说明（规则变更将自动生成新版本）" />
            </a-form-item>
          </a-form>
        </a-tab-pane>

        <!-- 步骤编排 -->
        <a-tab-pane key="steps" :tab="`步骤编排（${steps.length}）`">
          <!-- 全局参数定义：定义一次对所有步骤生效 -->
          <a-form layout="vertical">
            <a-form-item>
              <template #label>
                全局参数定义
                <span class="label-tip">对所有步骤生效，脚本中以 <code v-pre>{{ 参数名 }}</code> 占位引用；固定值参数提交人不可见不可改</span>
              </template>
              <div v-for="(p, pi) in globalParams" :key="pi" class="param-row">
                <a-input v-model:value="p.name" placeholder="参数名 *" class="param-name" />
                <a-input v-model:value="p.label" placeholder="显示名" class="param-label" />
                <a-input v-model:value="p.default" placeholder="默认值" class="param-default" />
                <a-checkbox v-model:checked="p.required">必填</a-checkbox>
                <a-checkbox v-model:checked="p.fixed">固定值</a-checkbox>
                <a-input v-model:value="p.description" placeholder="参数说明" class="param-desc" />
                <a-button size="small" danger @click="globalParams.splice(pi, 1)"><DeleteOutlined /></a-button>
              </div>
              <a-button size="small" class="op-btn-green" @click="addParam"><PlusOutlined />添加参数</a-button>
            </a-form-item>
          </a-form>
          <a-collapse v-model:active-key="stepActive">
            <a-collapse-panel v-for="(s, i) in steps" :key="i">
              <template #header><a-tag color="cyan">步骤 {{ i + 1 }}</a-tag>{{ s.name || '（未命名）' }}</template>
              <template #extra>
                <a-space @click.stop>
                  <a-button size="small" :disabled="i === 0" @click="moveStep(i, -1)"><UpOutlined /></a-button>
                  <a-button size="small" :disabled="i === steps.length - 1" @click="moveStep(i, 1)"><DownOutlined /></a-button>
                  <a-button size="small" danger :disabled="steps.length <= 1" @click="removeStep(i)"><DeleteOutlined /></a-button>
                </a-space>
              </template>
              <a-form layout="vertical">
                <div class="form-row">
                  <a-form-item label="步骤名" required class="form-col">
                    <a-input v-model:value="s.name" placeholder="如 重启服务" />
                  </a-form-item>
                  <a-form-item label="脚本类型" class="form-col-sm">
                    <a-select v-model:value="s.script_type" :options="scriptTypeOptions" />
                  </a-form-item>
                  <a-form-item label="执行凭据" required class="form-col">
                    <a-select
                      v-model:value="s.credential_id"
                      placeholder="SSH 执行凭据"
                      :options="credentials.map((c) => ({ label: c.name, value: c.id }))"
                    />
                  </a-form-item>
                  <a-form-item label="超时（秒）" class="form-col-sm">
                    <a-input-number v-model:value="s.timeout" :min="1" :max="86400" class="full-w" />
                  </a-form-item>
                </div>
                <a-form-item label="脚本内容" required>
                  <CodeEditor v-model="s.content" :lang="editorLang(s.script_type)" height="220px" />
                </a-form-item>
              </a-form>
            </a-collapse-panel>
          </a-collapse>
          <a-button type="dashed" block class="add-step-btn" @click="addStep"><PlusOutlined />添加步骤</a-button>
        </a-tab-pane>

        <!-- 执行策略与审批规则 -->
        <a-tab-pane key="flow" tab="执行策略与审批">
          <a-form layout="vertical">
            <div class="section-title">执行策略（提交时快照到工单）</div>
            <div class="form-row">
              <a-form-item label="并发数" class="form-col-sm">
                <a-input-number v-model:value="form.exec_strategy.concurrency" :min="1" :max="100" class="full-w" />
              </a-form-item>
              <a-form-item label="分批大小（0=不分批）" class="form-col-sm">
                <a-input-number v-model:value="form.exec_strategy.batch_size" :min="0" :max="1000" class="full-w" />
              </a-form-item>
              <a-form-item label="总超时（秒）" class="form-col-sm">
                <a-input-number v-model:value="form.exec_strategy.timeout" :min="1" :max="86400" class="full-w" />
              </a-form-item>
              <a-form-item label=" " class="form-col checks">
                <a-checkbox v-model:checked="form.exec_strategy.batch_pause">批间暂停</a-checkbox>
                <a-checkbox v-model:checked="form.exec_strategy.fail_fast">失败即停</a-checkbox>
                <a-checkbox v-model:checked="form.exec_strategy.kill_on_stop">终止杀进程</a-checkbox>
              </a-form-item>
            </div>

            <div class="section-title">
              审批规则
              <a-switch v-model:checked="form.approval_enabled" checked-children="开" un-checked-children="免审" class="approval-switch" />
            </div>
            <template v-if="form.approval_enabled">
              <!-- 审批节点=角色，节点间依次推进；节点内 V1 仅或签生效 -->
              <div v-for="(n, i) in nodes" :key="i" class="param-row">
                <a-tag color="cyan">节点 {{ i + 1 }}</a-tag>
                <a-select
                  v-model:value="n.role_id"
                  placeholder="审批角色 *"
                  class="node-role"
                  :options="roles.map((r) => ({ label: r.name, value: r.id }))"
                />
                <a-select v-model:value="n.approve_mode" class="node-mode" :options="approveModeOptions" />
                <a-button size="small" danger @click="nodes.splice(i, 1)"><DeleteOutlined /></a-button>
              </div>
              <a-button size="small" class="op-btn-green" @click="addNode"><PlusOutlined />添加审批节点</a-button>
              <div class="flag-line">
                <a-checkbox v-model:checked="form.allow_withdraw">允许提交人撤回</a-checkbox>
                <a-checkbox v-model:checked="form.allow_transfer">允许转审（预留）</a-checkbox>
                <a-checkbox v-model:checked="form.allow_countersign">允许加签（预留）</a-checkbox>
              </div>
            </template>
            <a-alert v-else type="warning" show-icon message="免审批：工单提交后直接进入执行" />
          </a-form>
        </a-tab-pane>

        <!-- 通知规则与权限范围 -->
        <a-tab-pane key="notify" tab="通知与权限">
          <a-form layout="vertical">
            <a-form-item>
              <template #label>
                通知规则
                <span class="label-tip">未配置的事件按默认策略通知（待审批→节点角色成员，通过/驳回→提交人）</span>
              </template>
              <div v-for="(r, i) in notifyRules" :key="i" class="param-row">
                <a-select v-model:value="r.event" placeholder="触发事件 *" class="rule-event" :options="eventOptions" />
                <a-select
                  v-model:value="r.receivers"
                  mode="multiple"
                  placeholder="收件人 *"
                  class="rule-receivers"
                  :options="receiverOptions(roles)"
                />
                <a-select v-model:value="r.channels" mode="multiple" placeholder="渠道 *" class="rule-channels" :options="channelOptions" />
                <a-button size="small" danger @click="notifyRules.splice(i, 1)"><DeleteOutlined /></a-button>
              </div>
              <a-button size="small" class="op-btn-green" @click="addNotifyRule"><PlusOutlined />添加通知规则</a-button>
            </a-form-item>
            <a-form-item>
              <template #label>
                可见角色范围
                <span class="label-tip">留空 = 全部角色可在工单中心使用该模板</span>
              </template>
              <a-select
                v-model:value="form.visible_role_ids"
                mode="multiple"
                placeholder="全部角色可见"
                :options="roles.map((r) => ({ label: r.name, value: r.id }))"
              />
            </a-form-item>
          </a-form>
        </a-tab-pane>
      </a-tabs>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.form-row {
  display: flex;
  gap: 16px;
}
.form-col {
  flex: 1;
}
.form-col-sm {
  width: 150px;
}
.full-w {
  width: 100%;
}
.checks {
  display: flex;
  align-items: flex-end;
}
.label-tip {
  font-size: 12px;
  color: var(--text-3);
  margin-left: 8px;
}
.param-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.param-name {
  width: 130px;
}
.param-label {
  width: 100px;
}
.param-default {
  width: 100px;
}
.param-desc {
  flex: 1;
}
.add-step-btn {
  margin-top: 12px;
}
.section-title {
  font-weight: 600;
  margin: 4px 0 12px;
}
.approval-switch {
  margin-left: 10px;
}
.node-role {
  width: 220px;
}
.node-mode {
  width: 140px;
}
.rule-event {
  width: 150px;
}
.rule-receivers {
  flex: 1;
}
.rule-channels {
  width: 240px;
}
.flag-line {
  display: flex;
  gap: 18px;
  margin-top: 14px;
}
</style>
