<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ResultPage } from '@/api/database'
import { formatCell, timeText } from './runtime'

const props = defineProps<{
  result: ResultPage
  loading?: boolean
  selectKey?: boolean
}>()
const emit = defineEmits<{
  page: [page: number, pageSize: number]
  pick: [row: unknown[]]
}>()
const preview = ref<string | null>(null)
// 以列序号作为键，重名列和二维原值不会在对象转换中丢失。
const columns = computed(() => [
  ...props.result.columns.map((column, index) => ({
    title: column.name,
    key: `column-${column.ordinal}-${index}`,
    dataIndex: index,
    width: 180,
    ellipsis: true
  })),
  ...(props.selectKey ? [{ title: '选择', key: 'pick', width: 100, dataIndex: -1, ellipsis: false }] : [])
])
const rows = computed(() => props.result.rows.map((row, index) => ({ key: `${props.result.page}-${index}`, values: row })))

function changePage(pagination: {
  current?: number
  pageSize?: number
}) {
  emit('page', pagination.current || 1, pagination.pageSize || 50)
}
</script>
<template>
  <div class="db-result">
    <p class="db-muted">已保存 {{ result.saved_total }} 条 · {{ result.returned_bytes }} 字节 ·
      {{ timeText(result.expires_at) }} 到期；总数为本次保存行数。</p>
    <a-alert v-if="result.truncated" type="warning" show-icon
      :message="`结果已截断：${result.truncate_reason || '达到上限，可能还有更多'}；翻页只查看本次已保存结果。`" />
    <a-table :columns="columns" :data-source="rows" :loading="loading" size="small"
      :scroll="{ x: 'max-content', y: 360 }"
      :pagination="{ current: result.page, pageSize: result.page_size, total: result.saved_total, showSizeChanger: true, pageSizeOptions: ['20','50','100'], showTotal: (n: number) => `已保存 ${n} 条` }"
      @change="changePage">
      <template #bodyCell="{ column, record }">
        <a-button v-if="column.key==='pick'" type="link" size="small"
          @click="emit('pick',record.values)">查看类型</a-button>
        <a-tooltip v-else :title="`列序号 ${Number(column.dataIndex) + 1}；点击查看单元格`">
          <span class="db-cell" tabindex="0" @click="preview = formatCell(record.values[column.dataIndex])"
            @keydown.enter="preview = formatCell(record.values[column.dataIndex])">
            {{ formatCell(record.values[column.dataIndex]) }}</span>
        </a-tooltip>
      </template>
    </a-table>
    <a-modal :open="preview !== null" title="单元格纯文本预览" :footer="null" @cancel="preview = null">
      <pre class="db-cell-preview">{{ preview }}</pre>
    </a-modal>
  </div>
</template>
