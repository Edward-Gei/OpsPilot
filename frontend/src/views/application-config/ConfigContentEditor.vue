<script setup lang="ts">
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

function update(value: string) { emit('update:modelValue', value) }
</script>

<template>
  <div class="config-editor">
    <a-alert v-if="format === 'text' && masked && !canReadSecret && !readonly" type="warning" show-icon
      message="原文已隐藏；保存时须完整替换内容" class="config-editor-alert" />
    <CodeEditor v-if="format === 'yaml' || format === 'json' || format === 'consul_kv'"
      :model-value="props.modelValue" :readonly="readonly" :indent-guides="true"
      :lang="format === 'yaml' ? 'yaml' : 'json'" height="360px" @update:model-value="update" />
    <a-textarea v-else :value="props.modelValue" :readonly="readonly" :rows="16"
      :placeholder="format === 'text' && masked && !canReadSecret ? '输入完整替换内容' : '配置内容'"
      class="config-editor-input" spellcheck="false" @update:value="update" />
  </div>
</template>

<style scoped>
.config-editor { min-width: 0; }
.config-editor-alert { margin-bottom: 10px; }
.config-editor-input { width: 100%; min-height: 260px; font-family: Consolas, 'Courier New', monospace; line-height: 1.55; resize: vertical; }
</style>
