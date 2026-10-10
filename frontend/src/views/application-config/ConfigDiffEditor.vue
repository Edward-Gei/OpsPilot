<script setup lang="ts">
// 版本与漂移共用只读差异编辑器；比较传入的已授权正文，不处理远端写入。
import { onBeforeUnmount, onMounted, ref, useSlots, watch } from 'vue'
import { basicSetup, EditorView } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { MergeView } from '@codemirror/merge'
import { StreamLanguage } from '@codemirror/language'
import { yaml } from '@codemirror/lang-yaml'
import { json } from '@codemirror/legacy-modes/mode/javascript'
import { tags } from '@lezer/highlight'
import { oneDark } from '@codemirror/theme-one-dark'
import type { ConfigFormat } from '@/api/applicationConfig'
import { useThemeStore } from '@/stores/theme'

const props = defineProps<{ left: string; right: string; format: ConfigFormat; leftLabel: string; rightLabel: string }>()
const emit = defineEmits<{ 'change-count': [count: number] }>()
const slots = useSlots()
const theme = useThemeStore()
const holder = ref<HTMLDivElement>()
let view: MergeView | null = null

function renderComparison() {
  if (!holder.value) return
  view?.destroy()
  const language = props.format === 'yaml' ? [yaml()] : ['json', 'consul_kv'].includes(props.format)
    ? [StreamLanguage.define({ ...json, tokenTable: { property: tags.propertyName } })] : []
  const extensions = [basicSetup, ...language, ...(theme.mode === 'dark' ? [oneDark] : []),
    EditorState.readOnly.of(true), EditorView.editable.of(false), EditorView.lineWrapping,
    EditorView.theme({
      '&': { fontSize: '13px' },
      '.cm-scroller': { fontFamily: "Consolas, 'Courier New', 'OpsPilot Noto Sans SC', monospace" },
      '&.cm-merge-a .cm-changedLine': { backgroundColor: 'var(--compare-deleted-bg)' },
      '&.cm-merge-b .cm-changedLine': { backgroundColor: 'var(--success-soft)' },
      '&.cm-merge-a .cm-changedText': { backgroundColor: 'var(--compare-deleted-bg)', textDecoration: 'line-through' },
      '&.cm-merge-b .cm-changedText': { backgroundColor: 'var(--success-soft)', textDecoration: 'underline' },
      '&.cm-merge-a .cm-changedLineGutter': { color: 'var(--error)', background: 'none' },
      '&.cm-merge-b .cm-changedLineGutter': { color: 'var(--success)', background: 'none' },
      '.cm-changeGutter': { width: '14px', textAlign: 'center' },
    })]
  view = new MergeView({
    parent: holder.value,
    a: { doc: props.left, extensions: [...extensions,
      EditorView.contentAttributes.of({ 'aria-label': props.leftLabel, 'aria-readonly': 'true', tabindex: '0' })] },
    b: { doc: props.right, extensions: [...extensions,
      EditorView.contentAttributes.of({ 'aria-label': props.rightLabel, 'aria-readonly': 'true', tabindex: '0' })] },
    highlightChanges: true, gutter: true,
  })
  emit('change-count', view.chunks.length)
}

onMounted(renderComparison)
watch(() => [props.left, props.right, props.format, props.leftLabel, props.rightLabel, theme.mode], renderComparison)
onBeforeUnmount(() => view?.destroy())
</script>

<template>
  <div class="config-diff" :class="{ 'with-controls': slots.controls }">
    <div class="compare-headings">
      <div><slot name="left-header">{{ leftLabel }}</slot></div>
      <div><slot name="right-header">{{ rightLabel }}</slot></div>
    </div>
    <div class="compare-editors">
      <div ref="holder" />
      <div v-if="slots.controls" class="compare-controls"><slot name="controls" /></div>
    </div>
  </div>
</template>

<style scoped>
.config-diff { --compare-deleted-bg: color-mix(in srgb, var(--error) 12%, transparent); min-width: 0; }
.compare-headings { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border: 1px solid var(--border); border-bottom: 0; border-radius: 8px 8px 0 0; background: var(--bg-card); }
.compare-headings > div { min-width: 0; padding: 10px 12px; overflow-wrap: anywhere; }
.compare-headings > div + div { border-left: 1px solid var(--border); }
.compare-editors { position: relative; border: 1px solid var(--border); border-radius: 0 0 8px 8px; overflow: hidden; }
.compare-editors :deep(.cm-mergeView) { height: min(520px, 55vh); overflow: auto; }
.compare-editors :deep(.cm-mergeViewEditor) { min-width: 0; }
.compare-editors :deep(.cm-mergeViewEditor + .cm-mergeViewEditor) { border-left: 1px solid var(--border); }
.compare-editors :deep(.cm-merge-a .cm-changedLineGutter::after) { content: '−'; }
.compare-editors :deep(.cm-merge-b .cm-changedLineGutter::after) { content: '+'; }
.compare-editors :deep(.cm-content:focus-visible) { outline: 2px solid var(--primary); outline-offset: -2px; }
.with-controls .compare-headings { column-gap: 72px; }
.with-controls :deep(.cm-mergeViewEditors) { gap: 72px; }
.compare-controls { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); }
@media (max-width: 740px) {
  .with-controls .compare-headings { column-gap: 0; }
  .with-controls :deep(.cm-mergeViewEditors) { gap: 0; }
  .with-controls .compare-editors { padding-bottom: 56px; }
  .compare-controls { top: auto; bottom: 8px; transform: translateX(-50%); }
}
</style>
