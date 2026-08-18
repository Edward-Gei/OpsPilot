<script setup lang="ts">
// 代码编辑器：CodeMirror 6 封装（shell/yaml 高亮、只读模式、跟随平台暗/浅主题）
// M3 模板内容编辑与版本回看共用；v-model 双向绑定
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { basicSetup, EditorView } from 'codemirror'
import { Compartment, EditorState } from '@codemirror/state'
import { StreamLanguage } from '@codemirror/language'
import { tags } from '@lezer/highlight'
import { shell } from '@codemirror/legacy-modes/mode/shell'
import { yaml } from '@codemirror/lang-yaml'
import { json as jsonParser } from '@codemirror/legacy-modes/mode/javascript'
import { oneDark } from '@codemirror/theme-one-dark'
import { useThemeStore } from '@/stores/theme'

const props = withDefaults(
  defineProps<{
    modelValue: string
    lang?: 'shell' | 'yaml' | 'json'
    readonly?: boolean
    height?: string
  }>(),
  { lang: 'shell', readonly: false, height: '320px' },
)
const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>()
const insertText = (text: string) => {
  if (!view) return
  const selection = view.state.selection.main
  view.dispatch({ changes: { from: selection.from, to: selection.to, insert: text }, selection: { anchor: selection.from + text.length } })
  view.focus()
}
defineExpose({ insertText })

const themeStore = useThemeStore()
const holder = ref<HTMLDivElement>()
let view: EditorView | null = null

// Compartment 允许运行时热替换语言/主题/只读配置，无需重建编辑器
const langComp = new Compartment()
const themeComp = new Compartment()
const readonlyComp = new Compartment()

/** 语言扩展：playbook 用 yaml，shell/adhoc 用 shell 流式高亮 */
function langExt(lang: 'shell' | 'yaml' | 'json') {
  if (lang === 'yaml') return yaml()
  if (lang === 'json') return StreamLanguage.define({ ...jsonParser, tokenTable: { property: tags.propertyName } })
  return StreamLanguage.define(shell)
}

/** 主题扩展：暗色用 oneDark，浅色用默认亮色并统一基础样式 */
function themeExt(mode: string) {
  const base = EditorView.theme({
    '&': { height: props.height, fontSize: '13px' },
    '.cm-scroller': { fontFamily: "Consolas, 'Courier New', monospace" },
  })
  return mode === 'dark' ? [oneDark, base] : [base]
}

onMounted(() => {
  view = new EditorView({
    parent: holder.value!,
    state: EditorState.create({
      doc: props.modelValue,
      extensions: [
        basicSetup,
        langComp.of(langExt(props.lang)),
        themeComp.of(themeExt(themeStore.mode)),
        readonlyComp.of([EditorState.readOnly.of(props.readonly), EditorView.editable.of(!props.readonly)]),
        // 编辑内容同步回 v-model
        EditorView.updateListener.of((u) => {
          if (u.docChanged) emit('update:modelValue', u.state.doc.toString())
        }),
      ],
    }),
  })
})

onBeforeUnmount(() => view?.destroy())

// 外部改值（如打开编辑弹窗回填）：仅在与编辑器内容不一致时整体替换
watch(
  () => props.modelValue,
  (val) => {
    if (view && val !== view.state.doc.toString()) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: val } })
    }
  },
)

// 语言 / 主题 / 只读 变化时热替换配置
watch(
  () => props.lang,
  (lang) => view?.dispatch({ effects: langComp.reconfigure(langExt(lang)) }),
)
watch(
  () => themeStore.mode,
  (mode) => view?.dispatch({ effects: themeComp.reconfigure(themeExt(mode)) }),
)
watch(
  () => props.readonly,
  (ro) =>
    view?.dispatch({
      effects: readonlyComp.reconfigure([EditorState.readOnly.of(ro), EditorView.editable.of(!ro)]),
    }),
)
</script>

<template>
  <div ref="holder" class="code-editor" />
</template>

<style scoped>
.code-editor {
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.code-editor :deep(.cm-editor) {
  outline: none;
}
</style>
