<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { ArrowLeftOutlined, ArrowRightOutlined } from '@ant-design/icons-vue'
import * as api from '@/api/applicationConfig'
import ConfigDiffEditor from './ConfigDiffEditor.vue'

const props = defineProps<{ open: boolean; fileId: number; canWrite: boolean }>()
const emit = defineEmits<{ 'update:open': [value: boolean]; resolved: [] }>()
const file = ref<api.ConfigFileBrief | null>(null)
const drift = ref<api.DriftView | null>(null)
const versions = ref<api.ConfigVersion[]>([])
const selectedVersionId = ref<number | undefined>()
const left = ref('')
const right = ref('')
const changeCount = ref(0)
const action = ref<'import' | 'publish' | null>(null)
const loading = ref(false)
const task = ref<api.ConfigTask | null>(null)
const canImport = computed(() => props.canWrite && drift.value?.external_exists)
const canPublish = computed(() => props.canWrite && drift.value?.baseline_version_id && drift.value.latest_snapshot_id)

async function load(versionId?: number) {
  loading.value = true
  try {
    const [configFile, view, history] = await Promise.all([
      api.getConfigFile(props.fileId), api.getDriftView(props.fileId, versionId), api.listVersions(props.fileId),
    ])
    file.value = configFile
    drift.value = view
    versions.value = history.items.filter((item) => item.status === 'published' || item.status === 'approved')
    selectedVersionId.value = view.baseline_version_id || undefined
    left.value = view.baseline_content || ''
    right.value = view.external_content || ''
    action.value = null
  } finally { loading.value = false }
}

watch(() => [props.open, props.fileId] as const, ([open]) => {
  if (open) { task.value = null; void load() }
}, { immediate: true })

async function chooseVersion(value: number) { await load(value) }
function previewImport() {
  if (!canImport.value) return
  left.value = drift.value?.external_content || ''
  right.value = drift.value?.external_content || ''
  action.value = 'import'
}
function previewPublish() {
  if (!canPublish.value) return
  left.value = drift.value?.baseline_content || ''
  right.value = drift.value?.baseline_content || ''
  action.value = 'publish'
}

async function confirm() {
  if (!action.value || !drift.value) return message.warning('请选择处理方向')
  loading.value = true
  try {
    if (action.value === 'import') {
      await api.importDrift(props.fileId)
      message.success('外部内容已导入 OpsPilot 正式版本')
      emit('resolved')
      emit('update:open', false)
      return
    }
    if (!drift.value.baseline_version_id || !drift.value.latest_snapshot_id) return
    task.value = await api.publishVersion(props.fileId, drift.value.baseline_version_id, drift.value.latest_snapshot_id)
    while (props.open && (task.value?.status === 'queued' || task.value?.status === 'running')) {
      await new Promise((resolve) => window.setTimeout(resolve, 1200))
      if (task.value) task.value = await api.getConfigTask(task.value.id)
    }
    if (task.value?.status === 'success') {
      message.success('重新发布成功，远端配置已更新')
      emit('resolved')
      emit('update:open', false)
    } else if (task.value && task.value.status !== 'queued' && task.value.status !== 'running') {
      message.warning(task.value.last_error || '发布未完成，漂移状态仍保留')
      await load()
      emit('resolved')
    }
  } finally { loading.value = false }
}
</script>

<template>
  <a-modal :open="open" centered :width="'min(1420px, calc(100vw - 32px))'" title="配置漂移处理"
    :body-style="{ padding: '16px 0', maxHeight: 'calc(100vh - 180px)', overflowY: 'auto' }"
    @cancel="emit('update:open', false)">
    <div v-if="file && drift" class="drift-body">
      <div class="drift-context">
        <strong>{{ file.name }}</strong><a-tag :color="drift.drift_status === 'remote_missing' ? 'error' : 'warning'">
          {{ drift.drift_status === 'remote_missing' ? '远端缺失' : drift.drift_status === 'drifted' ? '存在漂移' : '已一致' }}
        </a-tag>
      </div>
      <div class="drift-legend"><span>共 {{ changeCount }} 处可见差异</span>
        <span>− 左侧：删除 / 修改前</span><span>+ 右侧：新增 / 修改后</span></div>
      <ConfigDiffEditor :left="left" :right="right" :format="file.content_format"
        left-label="OpsPilot 版本内容" right-label="外部配置内容" @change-count="changeCount = $event">
        <template #left-header>
          <div class="drift-pane-head"><b>OpsPilot 版本</b>
            <a-select v-if="versions.length" :value="selectedVersionId" size="small" class="version-select"
              :options="versions.map((item) => ({ label: `v${item.version_no} (${item.status})`, value: item.id }))"
              @change="chooseVersion" />
          </div>
        </template>
        <template #right-header>
          <div class="drift-pane-head"><b>外部配置</b><span v-if="!drift.external_exists && action !== 'publish'">远端缺失</span></div>
        </template>
        <template #controls>
          <div class="drift-transfer" role="group" aria-label="漂移处理方式">
            <a-tooltip title="导入外部内容为新版本">
              <a-button :disabled="!canImport || loading" aria-label="导入外部内容为新版本" @click="previewImport"><ArrowLeftOutlined /></a-button>
            </a-tooltip>
            <a-tooltip title="重新发布 OpsPilot 版本">
              <a-button :disabled="!canPublish || loading" aria-label="重新发布 OpsPilot 版本" @click="previewPublish"><ArrowRightOutlined /></a-button>
            </a-tooltip>
          </div>
        </template>
      </ConfigDiffEditor>
      <a-alert v-if="task && task.status !== 'success'" class="drift-task" type="warning" show-icon
        :message="task.status === 'queued' || task.status === 'running' ? '正在发布并回读外部配置' : task.last_error || '发布失败'" />
    </div>
    <template #footer>
      <a-space><a-button @click="emit('update:open', false)">取消</a-button>
        <a-button type="primary" :loading="loading" :disabled="!action" @click="confirm">确认</a-button></a-space>
    </template>
  </a-modal>
</template>

<style scoped>
.drift-body { min-width: 0; }
.drift-context { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.drift-legend { display: flex; flex-wrap: wrap; gap: 8px 20px; margin-bottom: 12px; color: var(--text-2); }
.drift-pane-head { min-height: 24px; display: flex; align-items: center; gap: 12px; justify-content: space-between; }
.version-select { width: min(220px, 65%); }
.drift-transfer { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; }
.drift-task { margin-top: 14px; }
@media (max-width: 740px) {
  .drift-pane-head { flex-wrap: wrap; }
  .version-select { width: 100%; }
  .drift-transfer { flex-direction: row; }
}
</style>
