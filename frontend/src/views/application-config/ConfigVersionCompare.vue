<script setup lang="ts">
// 历史版本与审批共用只读差异视图，正文由后端完成授权和脱敏。
import { computed, ref } from 'vue'
import type { ConfigVersionComparison } from '@/api/applicationConfig'
import ConfigDiffEditor from './ConfigDiffEditor.vue'

const props = defineProps<{ comparison: ConfigVersionComparison }>()
const changeCount = ref(0)
const hiddenChanges = computed(() => props.comparison.has_changes && props.comparison.base_content === props.comparison.content)
</script>

<template>
  <div class="version-compare">
    <a-alert v-if="hiddenChanges" type="warning" show-icon message="存在变更，但差异涉及已脱敏内容，当前权限无法查看具体变化。" />
    <a-alert v-else-if="!comparison.has_changes" type="info" show-icon message="与旧正式版本内容一致，无变更。" />
    <div class="compare-legend">
      <span v-if="!hiddenChanges && comparison.has_changes">共 {{ changeCount }} 处变更</span>
      <span>− 左侧：删除 / 修改前</span><span>+ 右侧：新增 / 修改后</span>
    </div>
    <ConfigDiffEditor :left="comparison.base_content" :right="comparison.content" :format="comparison.content_format"
      left-label="旧正式版本内容" right-label="当前查看版本内容" @change-count="changeCount = $event">
      <template #left-header>{{ comparison.base_version_no === null ? '旧正式版本：空内容（无旧正式版本）' : `旧正式版本 v${comparison.base_version_no}` }}</template>
      <template #right-header>当前查看版本 v{{ comparison.version_no }}</template>
    </ConfigDiffEditor>
  </div>
</template>

<style scoped>
.compare-legend { display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 12px 0; color: var(--text-2); }
</style>
