<script setup lang="ts">
// 渠道×事件消息模板编辑器：不同发送协议使用不同字段，默认模板只读展示。
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import CodeEditor from '@/components/CodeEditor.vue'

type TemplateEvent = { key: string; label: string }
type TemplateVariable = { key: string; label: string; type: string; group: string; events: string[] }

const props = defineProps<{
  config: Record<string, any>
  channelType: 'email' | 'webhook' | 'teams'
  events: TemplateEvent[]
  defaults: Record<string, Record<string, string>>
  variables: TemplateVariable[]
  saveVersion?: number
}>()
const emit = defineEmits<{ (e: 'event-change', event: string): void }>()

const selectedEvent = ref(props.events[0]?.key || '')
const dirty = reactive<Record<string, boolean>>({})
const drafts = reactive<Record<string, Record<string, string>>>({})
const editor = ref<InstanceType<typeof CodeEditor>>()
const lastEmailField = ref<'title' | 'content'>('content')
const jsonError = ref('')

const templates = computed<Record<string, Record<string, string>>>({
  get: () => props.config.templates || {},
  set: (value) => { props.config.templates = value },
})
const currentDraft = computed(() => drafts[selectedEvent.value] || {})
const currentDefault = computed(() => props.defaults[selectedEvent.value] || {})
const currentVariables = computed(() => props.variables.filter((v) => v.events.includes(selectedEvent.value)))
const currentGroupVariables = computed(() => {
  const groups: Record<string, TemplateVariable[]> = {}
  for (const variable of currentVariables.value) (groups[variable.group] ||= []).push(variable)
  return groups
})
const isJson = computed(() => props.channelType !== 'email')
const jsonField = computed(() => (props.channelType === 'webhook' ? 'body' : 'card'))
const editorValue = computed({
  get: () => currentDraft.value[jsonField.value] || '',
  set: (value: string) => updateDraft(jsonField.value, value),
})

function syncDrafts() {
  const source = props.config.templates || {}
  for (const event of props.events) {
    if (dirty[event.key]) continue
    drafts[event.key] = { ...(source[event.key] || {}) }
  }
}
watch(() => props.config.templates, syncDrafts, { deep: true, immediate: true })
watch(() => props.events, () => {
  if (!props.events.some((event) => event.key === selectedEvent.value)) selectedEvent.value = props.events[0]?.key || ''
  syncDrafts()
}, { deep: true })
watch(() => props.saveVersion, (version, previous) => {
  if (version !== undefined && version !== previous) {
    for (const event of props.events) dirty[event.key] = false
  }
})

function updateDraft(field: string, value: string) {
  const next = { ...(drafts[selectedEvent.value] || {}), [field]: value }
  drafts[selectedEvent.value] = next
  props.config.templates = { ...templates.value, [selectedEvent.value]: next }
  dirty[selectedEvent.value] = true
  jsonError.value = ''
}

function hasCustom(event: string) {
  return Boolean(templates.value[event] && Object.keys(templates.value[event]).length)
}

function selectEvent(event: string) {
  if (event === selectedEvent.value) return
  if (Object.values(dirty).some(Boolean)) {
    const confirmed = window.confirm('当前模板有未保存修改，切换事件后仍会保留当前修改。是否继续？')
    if (!confirmed) return
  }
  selectedEvent.value = event
  emit('event-change', event)
  jsonError.value = ''
  nextTick(() => editor.value)
}

function useDefault() {
  const next = { ...currentDefault.value }
  drafts[selectedEvent.value] = next
  props.config.templates = { ...templates.value, [selectedEvent.value]: next }
  dirty[selectedEvent.value] = true
  jsonError.value = ''
}

function restoreDefault() {
  const next = { ...templates.value }
  delete next[selectedEvent.value]
  props.config.templates = next
  drafts[selectedEvent.value] = {}
  dirty[selectedEvent.value] = true
  jsonError.value = ''
}

function insertVariable(variable: TemplateVariable) {
  if (isJson.value) {
    editor.value?.insertText(`{${variable.key}}`)
    return
  }
  const field = lastEmailField.value
  updateDraft(field, `${currentDraft.value[field] || ''}{${variable.key}}`)
}

function replacementTemplate(template: string) {
  return template.replace(/\{(\w+)\}/g, (whole, key: string, offset: number, source: string) => {
    let inString = false
    let escaped = false
    for (const char of source.slice(0, offset)) {
      if (escaped) escaped = false
      else if (char === '\\') escaped = true
      else if (char === '"') inString = !inString
    }
    const variable = props.variables.find((item) => item.key === key)
    if (inString) return variable?.type === 'string' ? 'sample' : whole
    if (variable?.type === 'number') return '0'
    if (variable?.type === 'array') return '[]'
    return whole
  })
}

function formatJson() {
  const source = editorValue.value
  try {
    const parsed = JSON.parse(replacementTemplate(source))
    editorValue.value = JSON.stringify(parsed, null, 2)
    jsonError.value = ''
  } catch (error: any) {
    jsonError.value = `JSON 校验失败：${error?.message || '格式无效'}`
    message.error(jsonError.value)
  }
}
</script>

<template>
  <div class="tpl-fields">
    <div class="tpl-head">消息模板<span>（按事件配置；未配置时使用系统默认模板）</span></div>
    <div class="tpl-layout">
      <aside class="tpl-events">
        <div class="tpl-section-label">通知事件</div>
        <button
          v-for="event in events"
          :key="event.key"
          type="button"
          class="tpl-event"
          :class="{ active: selectedEvent === event.key }"
          @click="selectEvent(event.key)"
        >
          <span>{{ event.label }}</span>
          <small>{{ dirty[event.key] ? '未保存' : hasCustom(event.key) ? '已自定义' : '系统默认' }}</small>
        </button>
      </aside>
      <section class="tpl-editor-area">
        <div class="tpl-editor-head">
          <div>
            <b>{{ events.find((event) => event.key === selectedEvent)?.label }}</b>
            <span>{{ isJson ? (channelType === 'teams' ? 'MessageCard JSON 模板' : '请求体 JSON 模板') : '邮件标题与正文模板' }}</span>
          </div>
          <a-space v-if="isJson" :size="8" wrap>
            <a-button size="small" @click="formatJson">格式化 / 校验</a-button>
            <a-button size="small" @click="restoreDefault">恢复系统默认</a-button>
          </a-space>
          <a-button v-else size="small" @click="restoreDefault">恢复系统默认</a-button>
        </div>

        <template v-if="channelType === 'email'">
          <a-form-item label="标题模板">
            <a-input :value="currentDraft.title || ''" :maxlength="200" placeholder="留空使用系统默认标题" @focus="lastEmailField = 'title'" @update:value="(value: string) => updateDraft('title', value)" />
          </a-form-item>
          <a-form-item label="正文模板">
            <a-textarea :value="currentDraft.content || ''" :maxlength="2000" :rows="7" placeholder="留空使用系统默认正文" @focus="lastEmailField = 'content'" @update:value="(value: string) => updateDraft('content', value)" />
          </a-form-item>
        </template>
        <template v-else>
          <CodeEditor ref="editor" v-model="editorValue" lang="json" height="400px" />
          <div v-if="jsonError" class="tpl-error">{{ jsonError }}</div>
          <div class="tpl-editor-tip">原生类型变量（number / array）请使用无引号形式；未知变量仅允许出现在字符串值中。</div>
        </template>

        <div class="tpl-vars">
          <div v-for="(items, group) in currentGroupVariables" :key="group" class="tpl-var-group">
            <span class="tpl-var-group-title">{{ group }}</span>
            <a-tag v-for="variable in items" :key="variable.key" class="tpl-var" @click="insertVariable(variable)">
              {{ '{' + variable.key + '}' }}<small> · {{ variable.type }}</small>
            </a-tag>
          </div>
        </div>

        <div class="tpl-default">
          <div class="tpl-default-head">
            <div><b>系统默认模板</b><span>当前事件未配置自定义模板时使用</span></div>
            <a-button size="small" type="link" @click="useDefault">使用此默认模板</a-button>
          </div>
          <CodeEditor v-if="isJson" :model-value="currentDefault[jsonField] || ''" lang="json" height="180px" readonly />
          <pre v-else class="tpl-default-text">{{ currentDefault.title || '（默认标题）' }}\n\n{{ currentDefault.content || '（默认正文）' }}</pre>
          <div class="tpl-editor-tip">默认模板为只读示例，点击“使用此默认模板”后才会复制到编辑器。</div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.tpl-fields { margin-top: 4px; padding-top: 12px; border-top: 1px dashed var(--border-color, #e5e7eb); }
.tpl-head { font-weight: 600; margin-bottom: 14px; }
.tpl-head span, .tpl-editor-head span, .tpl-default-head span { font-weight: 400; font-size: 12px; color: var(--text-3); margin-left: 6px; }
.tpl-layout { display: grid; grid-template-columns: 190px minmax(0, 1fr); gap: 20px; }
.tpl-events { border-right: 1px solid var(--border); padding-right: 14px; }
.tpl-section-label { color: var(--text-3); font-size: 12px; font-weight: 600; margin-bottom: 8px; }
.tpl-event { width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 38px; padding: 0 9px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--text-2); cursor: pointer; text-align: left; }
.tpl-event:hover, .tpl-event.active { color: var(--primary); background: var(--bg-hover); border-color: var(--border); }
.tpl-event small { color: var(--text-3); white-space: nowrap; font-size: 11px; }
.tpl-editor-area { min-width: 0; }
.tpl-editor-head, .tpl-default-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.tpl-editor-head b, .tpl-default-head b { color: var(--text-1); font-size: 14px; }
.tpl-editor-head span, .tpl-default-head span { display: block; margin: 2px 0 0; }
.tpl-editor-area :deep(.ant-form-item) { margin-bottom: 14px; }
.tpl-editor-tip { color: var(--text-3); font-size: 12px; line-height: 1.7; margin-top: 8px; }
.tpl-error { color: var(--ant-color-error, #ff4d4f); margin-top: 8px; font-size: 12px; }
.tpl-vars { display: flex; flex-direction: column; gap: 8px; margin: 14px 0; }
.tpl-var-group { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 4px; }
.tpl-var-group-title { color: var(--text-3); font-size: 12px; margin-right: 4px; }
.tpl-var { cursor: pointer; user-select: none; }
.tpl-var:hover { color: var(--primary); border-color: var(--primary); }
.tpl-var small { color: var(--text-3); }
.tpl-default { border-top: 1px dashed var(--border); padding-top: 16px; }
.tpl-default-text { min-height: 180px; margin: 0; padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-hover); color: var(--text-2); white-space: pre-wrap; font: 12px/1.6 Consolas, monospace; }
@media (max-width: 760px) { .tpl-layout { grid-template-columns: 1fr; } .tpl-events { border-right: 0; border-bottom: 1px solid var(--border); padding: 0 0 12px; display: grid; grid-template-columns: 1fr 1fr; gap: 4px; } .tpl-section-label { grid-column: 1 / -1; } }
</style>
