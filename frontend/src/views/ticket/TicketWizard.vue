<script setup lang="ts">
// 工单提交向导（V2）：选模板 → 填参数 → 提交
// 工单中心只能使用模板：标题=模板名，作业主机/步骤/审批/策略全部来自模板规则（提交时固化快照）
import { computed, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import * as ticketApi from '@/api/ticket'
import { approveModeText, typeText } from '../job/meta'
import type { TemplateType } from '@/api/job'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ 'update:open': [boolean]; saved: [] }>()

const step = ref(0) // 0=选模板 1=填参数并确认

// ---------- 第一步：可用模板（enabled + 可见范围过滤后） ----------
const tplLoading = ref(false)
const templates = ref<ticketApi.UsableTemplate[]>([])
const keyword = ref('')
const selectedId = ref<number | null>(null)

const filteredTemplates = computed(() =>
  keyword.value
    ? templates.value.filter((t) => t.name.includes(keyword.value))
    : templates.value,
)

async function loadTemplates() {
  tplLoading.value = true
  try {
    templates.value = (await ticketApi.listUsableTemplates()).items
  } finally {
    tplLoading.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (!open) return
    step.value = 0
    selectedId.value = null
    keyword.value = ''
    form.value = null
    loadTemplates()
  },
)

// ---------- 第二步：表单描述（汇总参数 + 规则只读预览） ----------
const formLoading = ref(false)
const form = ref<ticketApi.TemplateFormDesc | null>(null)
const paramValues = reactive<Record<string, string>>({})
const paramOptions = reactive<Record<string, string[]>>({})
const prepareId = ref<string | undefined>()

/** 选定模板进入第二步：拉取表单描述并按默认值初始化参数 */
async function next() {
  if (!selectedId.value) {
    message.warning('请先选择一个模板')
    return
  }
  formLoading.value = true
  try {
    const templateForm = await ticketApi.getTemplateFormDesc(selectedId.value)
    const prepared = await ticketApi.prepareTicket(selectedId.value, {})
    form.value = templateForm
    Object.keys(paramValues).forEach((k) => delete paramValues[k])
    Object.keys(paramOptions).forEach((k) => delete paramOptions[k])
    Object.assign(paramOptions, prepared.options)
    prepareId.value = prepared.prepare_id
    for (const p of form.value.params) {
      paramValues[p.name] = p.source === 'generated'
        ? prepared.values[p.name] ?? prepared.options[p.name]?.[0] ?? ''
        : p.default ?? ''
    }
    step.value = 1
  } catch {
    /* 禁用 40901 / 范围外 40302 等提示由拦截器统一弹出 */
  } finally {
    formLoading.value = false
  }
}

/** 执行策略一行摘要 */
const strategyText = computed(() => {
  const s = form.value?.exec_strategy
  if (!s) return '—'
  return [
    `超时 ${s.timeout}s`,
    s.fail_fast ? '失败即停' : '失败继续',
  ].join(' · ')
})

// ---------- 提交（只传 template_id + params，TICKET-01） ----------
const submitting = ref(false)

async function onSubmit() {
  if (!form.value) return
  // 必填参数前端预校验（后端 40001 兜底）
  for (const p of form.value.params) {
    if (p.required && p.source !== 'generated' && !paramValues[p.name]?.trim()) {
      message.warning(`请填写必填参数「${p.label || p.name}」`)
      return
    }
  }
  submitting.value = true
  try {
    // 空值参数不传，由后端补默认值
    const params: Record<string, string> = {}
    for (const [k, v] of Object.entries(paramValues)) {
      if (v.trim()) params[k] = v
    }
    if (!prepareId.value) {
      message.warning('动态参数尚未生成，请返回上一步重试')
      return
    }
    const res = await ticketApi.createTicket(form.value.template.id, params, prepareId.value)
    if (res.status === 'queued') message.success(`工单 ${res.ticket_no} 已提交，免审进入执行队列`)
    else message.success(`工单 ${res.ticket_no} 已提交，等待第 ${res.current_node} 节点审批`)
    emit('update:open', false)
    emit('saved')
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    submitting.value = false
  }
}

/** 作业主机一行摘要（模板绑定，提交时固化快照） */
const jobHostText = computed(() => {
  const h = form.value?.job_host
  if (!h) return '—'
  return `${h.name}（${h.ip}:${h.ssh_port}）`
})
</script>

<template>
  <a-modal
    :open="open"
    title="提交工单"
    :width="860"
    :footer="null"
    @update:open="(v: boolean) => emit('update:open', v)"
  >
    <a-steps :current="step" size="small" class="w-steps">
      <a-step title="选择模板" />
      <a-step title="填写参数并确认" />
    </a-steps>

    <!-- 第一步：模板卡片列表 -->
    <template v-if="step === 0">
      <a-input
        v-model:value="keyword"
        placeholder="搜索模板名"
        allow-clear
        class="w-kw"
      />
      <a-spin :spinning="tplLoading">
        <a-empty v-if="!filteredTemplates.length" description="暂无可用模板（需管理员在模板管理中启用并授权）" />
        <div v-else class="tpl-grid">
          <div
            v-for="t in filteredTemplates"
            :key="t.id"
            class="tpl-card"
            :class="{ 'tpl-card--active': selectedId === t.id }"
            @click="selectedId = t.id"
          >
            <div class="tpl-card-head">
              <b>{{ t.name }}</b>
              <a-tag :color="typeText[t.type as TemplateType]?.color">
                {{ typeText[t.type as TemplateType]?.text || t.type }}
              </a-tag>
            </div>
            <div class="tpl-card-desc">{{ t.description || '暂无说明' }}</div>
            <div class="tpl-card-foot">
              <a-tag color="cyan">{{ t.process_name || '流程模板' }}</a-tag>
              <a-tag :color="t.approval_enabled ? 'gold' : 'default'">{{ t.approval_enabled ? '需审批' : '免审' }}</a-tag>
            </div>
          </div>
        </div>
      </a-spin>
      <div class="w-actions">
        <a-button @click="emit('update:open', false)">取消</a-button>
        <a-button type="primary" :disabled="!selectedId" :loading="formLoading" @click="next">下一步</a-button>
      </div>
    </template>

    <!-- 第二步：参数表单 + 规则只读预览 -->
    <template v-else-if="form">
      <div class="w-tpl-head">
        <b>{{ form.template.name }}</b>
        <a-tag color="cyan">{{ form.template.process_name }}</a-tag>
        <span class="w-tpl-tip">标题将使用模板名，提交后作业主机/脚本/审批流固化为快照</span>
      </div>

      <!-- 汇总参数（同名合并，fixed 参数不外显） -->
      <div class="w-section">填写参数（{{ form.params.length }}）</div>
      <a-empty v-if="!form.params.length" description="该模板无需填写参数" :image-style="{ height: '48px' }" />
      <a-form v-else layout="vertical" class="param-form">
        <a-form-item v-for="p in form.params" :key="p.name" :required="p.required">
          <template #label>
            {{ p.label || p.name }}
            <span class="w-param-name">{{ p.name }}</span>
            <span v-if="p.description" class="w-param-desc">{{ p.description }}</span>
          </template>
          <a-select
            v-if="paramOptions[p.name]?.length || p.input_type === 'enum'"
            v-model:value="paramValues[p.name]"
            show-search
            option-filter-prop="label"
            :options="(paramOptions[p.name] || p.options || []).map((v) => ({ label: v, value: v }))"
          />
          <a-input v-else-if="p.source === 'generated'" v-model:value="paramValues[p.name]" disabled />
          <a-input v-else v-model:value="paramValues[p.name]" :placeholder="p.default ? `默认：${p.default}` : ''" />
        </a-form-item>
      </a-form>

      <!-- 规则只读预览 -->
      <a-descriptions bordered size="small" :column="2" class="w-desc">
        <a-descriptions-item label="作业主机">{{ jobHostText }}</a-descriptions-item>
        <a-descriptions-item label="执行策略">{{ strategyText }}</a-descriptions-item>
        <a-descriptions-item label="执行步骤" :span="2">
          <a-tag v-for="s in form.steps" :key="s.step_order" color="geekblue">
            {{ s.step_order }}. {{ s.name }}
          </a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="审批流" :span="2">
          <template v-if="form.flow?.length">
            <a-tag v-for="n in form.flow" :key="n.node" color="gold">
              节点{{ n.node }}：{{ n.role_name }}（{{ approveModeText[n.approve_mode as keyof typeof approveModeText] || n.approve_mode }}）
            </a-tag>
          </template>
          <a-tag v-else color="orange">免审批，提交后直接执行</a-tag>
        </a-descriptions-item>
      </a-descriptions>

      <div class="w-actions">
        <a-button @click="step = 0">上一步</a-button>
        <a-button type="primary" :loading="submitting" @click="onSubmit">提交工单</a-button>
      </div>
    </template>
  </a-modal>
</template>

<style scoped>
.w-steps {
  margin-bottom: 18px;
}
.w-kw {
  width: 240px;
  margin-bottom: 14px;
}
.tpl-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
  max-height: 420px;
  overflow-y: auto;
}
.tpl-card {
  border: 1px solid var(--border-1, #d9d9d9);
  border-radius: 10px;
  padding: 12px 14px;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.tpl-card:hover {
  border-color: var(--ant-primary-color, #1677ff);
}
.tpl-card--active {
  border-color: var(--ant-primary-color, #1677ff);
  box-shadow: 0 0 0 2px rgba(22, 119, 255, 0.15);
}
.tpl-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.tpl-card-desc {
  font-size: 12px;
  color: var(--text-3);
  margin: 6px 0;
  min-height: 18px;
}
.tpl-card-foot {
  display: flex;
  gap: 4px;
}
.w-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}
.w-tpl-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.w-tpl-tip {
  font-size: 12px;
  color: var(--text-3);
}
.w-section {
  font-weight: 600;
  margin: 14px 0 10px;
}
.param-form :deep(.ant-form-item) {
  margin-bottom: 12px;
}
.w-param-name {
  font-size: 12px;
  color: var(--text-3);
  margin-left: 6px;
  font-family: Consolas, 'Courier New', monospace;
}
.w-param-desc {
  font-size: 12px;
  color: var(--text-3);
  margin-left: 8px;
}
.w-desc {
  margin-top: 6px;
}
</style>
