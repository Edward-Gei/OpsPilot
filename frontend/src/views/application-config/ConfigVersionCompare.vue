<script setup lang="ts">
// 历史版本与审批共用只读差异视图，正文由后端完成授权和脱敏。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { basicSetup, EditorView } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { MergeView } from '@codemirror/merge'
import { StreamLanguage } from '@codemirror/language'
import { yaml } from '@codemirror/lang-yaml'
import { json } from '@codemirror/legacy-modes/mode/javascript'
import { tags } from '@lezer/highlight'
import { oneDark } from '@codemirror/theme-one-dark'
import type { ConfigVersionComparison } from '@/api/applicationConfig'
import { useThemeStore } from '@/stores/theme'

const props = defineProps<{ comparison: ConfigVersionComparison }>()
const theme = useThemeStore()
const holder = ref<HTMLDivElement>()
const changeCount = ref(0)
const hiddenChanges = computed(() => props.comparison.has_changes && props.comparison.base_content === props.comparison.content)
let view: MergeView | null = null

function renderComparison() {
  if (!holder.value) return
  view?.destroy()
  const format = props.comparison.content_format
  const language = format === 'yaml' ? [yaml()] : ['json', 'consul_kv'].includes(format)
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
    a: { doc: props.comparison.base_content, extensions: [...extensions,
      EditorView.contentAttributes.of({ 'aria-label': '旧正式版本内容', 'aria-readonly': 'true', tabindex: '0' })] },
    b: { doc: props.comparison.content, extensions: [...extensions,
      EditorView.contentAttributes.of({ 'aria-label': '当前查看版本内容', 'aria-readonly': 'true', tabindex: '0' })] },
    highlightChanges: true, gutter: true,
  })
  changeCount.value = view.chunks.length
}

onMounted(renderComparison)
watch(() => [props.comparison, theme.mode], renderComparison)
onBeforeUnmount(() => view?.destroy())
</script>

<template>
  <div class="version-compare">
    <a-alert v-if="hiddenChanges" type="warning" show-icon message="存在变更，但差异涉及已脱敏内容，当前权限无法查看具体变化。" />
    <a-alert v-else-if="!comparison.has_changes" type="info" show-icon message="与旧正式版本内容一致，无变更。" />
    <div class="compare-legend">
      <span v-if="!hiddenChanges && comparison.has_changes">共 {{ changeCount }} 处变更</span>
      <span>− 左侧：删除 / 修改前</span><span>+ 右侧：新增 / 修改后</span>
    </div>
    <div class="compare-headings">
      <span>{{ comparison.base_version_no === null ? '旧正式版本：空内容（无旧正式版本）' : `旧正式版本 v${comparison.base_version_no}` }}</span>
      <span>当前查看版本 v{{ comparison.version_no }}</span>
    </div>
    <div ref="holder" class="compare-editors" />
  </div>
</template>

<style scoped>
.version-compare { --compare-deleted-bg: color-mix(in srgb, var(--error) 12%, transparent); }
.compare-legend { display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 12px 0; color: var(--text-2); }
.compare-headings { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border: 1px solid var(--border); border-bottom: 0; border-radius: 8px 8px 0 0; background: var(--bg-card); }
.compare-headings span { padding: 10px 12px; overflow-wrap: anywhere; }
.compare-headings span + span { border-left: 1px solid var(--border); }
.compare-editors { border: 1px solid var(--border); border-radius: 0 0 8px 8px; overflow: hidden; }
.compare-editors :deep(.cm-mergeView) { height: min(520px, 55vh); overflow: auto; }
.compare-editors :deep(.cm-mergeViewEditor) { min-width: 0; }
.compare-editors :deep(.cm-mergeViewEditor + .cm-mergeViewEditor) { border-left: 1px solid var(--border); }
.compare-editors :deep(.cm-merge-a .cm-changedLineGutter::after) { content: '−'; }
.compare-editors :deep(.cm-merge-b .cm-changedLineGutter::after) { content: '+'; }
.compare-editors :deep(.cm-content:focus-visible) { outline: 2px solid var(--primary); outline-offset: -2px; }
</style>
