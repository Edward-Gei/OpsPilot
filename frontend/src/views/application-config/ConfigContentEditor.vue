<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { CloseOutlined, DownOutlined, SearchOutlined, UpOutlined } from '@ant-design/icons-vue'
import type { ConfigFormat } from '@/api/applicationConfig'
import CodeEditor from '@/components/CodeEditor.vue'

const props = defineProps<{
  format: ConfigFormat
  modelValue: string
  masked?: boolean
  readonly?: boolean
  canReadSecret?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const frame = ref<HTMLDivElement>()
const codeEditor = ref<InstanceType<typeof CodeEditor>>()
const searchOpen = ref(false)
const searchText = ref('')
const matchIndex = ref(0)
const matches = computed(() => {
  if (!searchText.value) return []
  const escaped = searchText.value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return [...props.modelValue.matchAll(new RegExp(escaped, 'gi'))].map((match) => match.index)
})

function update(value: string) { emit('update:modelValue', value) }

/** 查找只变更选区，绝不修改配置正文。 */
function revealMatch() {
  const from = matches.value[matchIndex.value]
  if (from === undefined) return
  const to = from + searchText.value.length
  codeEditor.value?.selectRange(from, to)
}

function navigate(step: number) {
  if (!matches.value.length) return
  matchIndex.value = (matchIndex.value + step + matches.value.length) % matches.value.length
  revealMatch()
}

function openSearch() {
  searchOpen.value = true
  void nextTick(() => frame.value?.querySelector('input')?.focus())
}

function closeSearch() {
  searchOpen.value = false
  searchText.value = ''
  codeEditor.value?.clearSelection()
}

watch([searchText, () => props.modelValue, () => props.format], () => {
  matchIndex.value = 0
  void nextTick(() => matches.value.length ? revealMatch() : codeEditor.value?.clearSelection())
})
</script>

<template>
  <div class="config-editor">
    <a-alert v-if="format === 'text' && masked && !canReadSecret && !readonly" type="warning" show-icon
      message="原文已隐藏；保存时须完整替换内容" class="config-editor-alert" />
    <div ref="frame" class="config-editor-frame" style="resize: vertical">
      <div v-if="readonly" class="config-editor-search">
        <template v-if="searchOpen">
          <a-input v-model:value="searchText" aria-label="查找内容" size="small" allow-clear
            placeholder="查找内容" @press-enter="navigate(1)" />
          <span class="config-editor-find-count">{{ matches.length ? `${matchIndex + 1} / ${matches.length}` : '0 / 0' }}</span>
          <a-button type="text" size="small" aria-label="上一个匹配" title="上一个匹配" :disabled="!matches.length" @click="navigate(-1)"><UpOutlined /></a-button>
          <a-button type="text" size="small" aria-label="下一个匹配" title="下一个匹配" :disabled="!matches.length" @click="navigate(1)"><DownOutlined /></a-button>
          <a-button type="text" size="small" aria-label="关闭查找" title="关闭查找" @click="closeSearch"><CloseOutlined /></a-button>
        </template>
        <a-button v-else type="text" size="small" aria-label="查找配置内容" title="查找配置内容" @click="openSearch"><SearchOutlined /></a-button>
      </div>
      <CodeEditor v-if="readonly || format !== 'text'" ref="codeEditor"
        :model-value="props.modelValue" :readonly="readonly" :indent-guides="format !== 'text'"
        :lang="format === 'yaml' ? 'yaml' : format === 'text' ? 'text' : 'json'" height="100%" @update:model-value="update" />
      <a-textarea v-else :value="props.modelValue" :readonly="readonly" :rows="16"
        :placeholder="format === 'text' && masked && !canReadSecret ? '输入完整替换内容' : '配置内容'"
        class="config-editor-input" spellcheck="false" @update:value="update" />
    </div>
  </div>
</template>

<style scoped>
.config-editor { min-width: 0; }
.config-editor-alert { margin-bottom: 10px; }
.config-editor-frame { display: flex; flex-direction: column; height: 360px; min-height: 180px; min-width: 0; overflow: hidden; border: 1px solid var(--border); border-radius: 8px; }
.config-editor-frame :deep(.code-editor) { flex: 1; min-height: 0; border: 0; border-radius: 0; }
.config-editor-search { display: flex; justify-content: flex-end; align-items: center; gap: 4px; min-height: 32px; padding: 2px 6px; border-bottom: 1px solid var(--border); }
.config-editor-search :deep(.ant-input) { flex: 1; min-width: 0; max-width: 240px; }
.config-editor-find-count { min-width: 48px; text-align: center; white-space: nowrap; }
.config-editor-input { flex: 1; width: 100%; min-height: 0; font-family: Consolas, 'Courier New', monospace; line-height: 1.55; resize: none; border: 0; border-radius: 0; }
</style>
