<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
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
const frameHeight = ref(360)
let dragStartY = 0
let dragStartHeight = 360

function update(value: string) { emit('update:modelValue', value) }

function stopResize() {
  window.removeEventListener('pointermove', resize)
  window.removeEventListener('pointerup', stopResize)
  window.removeEventListener('pointercancel', stopResize)
}

function resize(event: PointerEvent) {
  frameHeight.value = Math.max(180, dragStartHeight + event.clientY - dragStartY)
}

function startResize(event: PointerEvent) {
  if (event.button !== 0) return
  event.preventDefault()
  dragStartY = event.clientY
  dragStartHeight = frameHeight.value
  window.addEventListener('pointermove', resize)
  window.addEventListener('pointerup', stopResize)
  window.addEventListener('pointercancel', stopResize)
}

function resizeByKeyboard(event: KeyboardEvent) {
  if (event.key === 'ArrowUp') frameHeight.value = Math.max(180, frameHeight.value - 20)
  else if (event.key === 'ArrowDown') frameHeight.value += 20
  else return
  event.preventDefault()
}

onBeforeUnmount(stopResize)
</script>

<template>
  <div class="config-editor">
    <a-alert v-if="format === 'text' && masked && !canReadSecret && !readonly" type="warning" show-icon
      message="原文已隐藏；保存时须完整替换内容" class="config-editor-alert" />
    <div class="config-editor-frame" :style="{ height: `${frameHeight}px` }">
      <CodeEditor v-if="readonly || format !== 'text'"
        :model-value="props.modelValue" :readonly="readonly" :focusable-readonly="readonly" :indent-guides="format !== 'text'"
        :lang="format === 'yaml' ? 'yaml' : format === 'text' ? 'text' : 'json'" height="100%" @update:model-value="update" />
      <a-textarea v-else :value="props.modelValue" :readonly="readonly" :rows="16"
        :placeholder="format === 'text' && masked && !canReadSecret ? '输入完整替换内容' : '配置内容'"
        class="config-editor-input" spellcheck="false" @update:value="update" />
      <div class="config-editor-resize-handle" role="separator" aria-label="调整配置内容高度"
        aria-orientation="horizontal" :aria-valuenow="frameHeight" aria-valuemin="180" tabindex="0"
        @pointerdown="startResize" @keydown="resizeByKeyboard" />
    </div>
  </div>
</template>

<style scoped>
.config-editor { min-width: 0; }
.config-editor-alert { margin-bottom: 10px; }
.config-editor-frame { display: flex; flex-direction: column; height: 360px; min-height: 180px; min-width: 0; overflow: hidden; border: 1px solid var(--border); border-radius: 8px; }
.config-editor-frame :deep(.code-editor) { flex: 1; min-height: 0; border: 0; border-radius: 0; }
.config-editor-input { flex: 1; width: 100%; min-height: 0; font-family: Consolas, 'Courier New', monospace; line-height: 1.55; resize: none; border: 0; border-radius: 0; }
.config-editor-resize-handle { flex: none; width: 100%; height: 10px; cursor: ns-resize; touch-action: none; }
</style>
