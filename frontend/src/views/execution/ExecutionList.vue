<script setup lang="ts">
// 执行中心（M5）：执行记录列表，行点击进入详情页（步骤列表 + 实时日志）
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import dayjs, { type Dayjs } from 'dayjs'
import {
  EyeOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons-vue'
import * as execApi from '@/api/execution'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { fmtTime } from '@/views/ticket/meta'
import { execStatusMeta, execStatusOptions } from './meta'

const router = useRouter()

// ---------- 列表 ----------
const loading = ref(false)
const items = ref<execApi.ExecutionBrief[]>([])
const total = ref(0)
const query = reactive({
  page: 1,
  page_size: 20,
  ticket_no: '',
  status: undefined as string | undefined,
  range: [
    dayjs().subtract(30, 'day').format('YYYY-MM-DD HH:mm:ss'),
    dayjs().format('YYYY-MM-DD HH:mm:ss'),
  ] as string[],
})

const rangePresets: { label: string; value: [Dayjs, Dayjs] }[] = [
  { label: '最近 7 天', value: [dayjs().subtract(7, 'day'), dayjs()] },
  { label: '最近 30 天', value: [dayjs().subtract(30, 'day'), dayjs()] },
  { label: '最近 90 天', value: [dayjs().subtract(90, 'day'), dayjs()] },
]

const columns = ref(makeResizable([
  { title: '执行 ID', dataIndex: 'id', key: 'id', width: 90 },
  { title: '工单号', dataIndex: 'ticket_no', key: 'ticket_no', width: 150 },
  { title: '标题', dataIndex: 'title', key: 'title', width: 200, ellipsis: true },
  { title: '作业主机', key: 'job_host', width: 130, ellipsis: true },
  { title: '状态', key: 'status', width: 100 },
  { title: '步骤数', key: 'scale', width: 110 },
  { title: '发起人', dataIndex: 'creator_name', key: 'creator_name', width: 110, ellipsis: true },
  { title: '开始时间', key: 'started', width: 155 },
  { title: '结束时间', key: 'finished', width: 155 },
  { title: '操作', key: 'action', width: 90, fixed: 'right' as const },
]))

/** 拉取执行记录（工单号模糊筛选） */
async function loadList() {
  loading.value = true
  try {
    const data = await execApi.listExecutions({
      page: query.page,
      page_size: query.page_size,
      ticket_no: query.ticket_no || undefined,
      status: query.status,
      start: query.range[0] || undefined,
      end: query.range[1] || undefined,
    })
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

// ---------- 横幅统计（全局口径，page_size=1 只取 total） ----------
const stats = reactive({ all: 0, running: 0, success: 0, failed: 0 })

async function loadStats() {
  const [all, running, success, failed] = await Promise.all([
    execApi.listExecutions({ page: 1, page_size: 1 }),
    execApi.listExecutions({ page: 1, page_size: 1, status: 'running' }),
    execApi.listExecutions({ page: 1, page_size: 1, status: 'success' }),
    execApi.listExecutions({ page: 1, page_size: 1, status: 'failed' }),
  ])
  stats.all = all.total
  stats.running = running.total
  stats.success = success.total
  stats.failed = failed.total
}

function refreshAll() {
  loadList()
  loadStats()
}

function onSearch() {
  query.page = 1
  loadList()
}

function onPageChange(page: number, pageSize: number) {
  query.page = page
  query.page_size = pageSize
  loadList()
}

/** 进入执行详情页（步骤列表 + 实时日志） */
function openDetail(row: execApi.ExecutionBrief) {
  router.push({ name: 'execution-detail', params: { id: row.id } })
}

onMounted(() => {
  refreshAll()
})
</script>

<template>
  <div>
    <!-- 彩色横幅：页面标识 + 全局统计（与 M1 页面同构） -->
    <div class="op-hero op-hero--indigo">
      <div class="op-hero-icon"><ThunderboltOutlined /></div>
      <div>
        <div class="op-hero-title">执行中心</div>
        <div class="op-hero-sub">工单执行记录：作业主机步骤列表、实时日志与执行控制</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat"><b>{{ stats.all }}</b><span>总执行</span></div>
        <div class="op-hero-stat"><b>{{ stats.running }}</b><span>执行中</span></div>
        <div class="op-hero-stat"><b>{{ stats.success }}</b><span>成功</span></div>
        <div class="op-hero-stat"><b>{{ stats.failed }}</b><span>失败</span></div>
      </div>
    </div>

    <!-- 工具栏 -->
    <div class="toolbar">
      <a-input
        v-model:value="query.ticket_no"
        placeholder="搜索工单号"
        class="kw"
        allow-clear
        @press-enter="onSearch"
      >
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-select
        v-model:value="query.status"
        placeholder="状态"
        class="status-sel"
        allow-clear
        :options="execStatusOptions"
        @change="onSearch"
      />
      <a-range-picker
        v-model:value="query.range"
        show-time
        value-format="YYYY-MM-DD HH:mm:ss"
        class="range"
        :placeholder="['开始时间', '结束时间']"
        :presets="rangePresets"
        @change="onSearch"
      />
    </div>

    <!-- 列表 -->
    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      row-key="id"
      bordered
      :scroll="{ x: 1390 }"
      @resize-column="onResizeColumn"
      :pagination="{
        current: query.page,
        pageSize: query.page_size,
        total,
        showSizeChanger: true,
        showQuickJumper: true,
        showTotal: (t: number) => `共 ${t} 条执行记录`,
        onChange: onPageChange,
      }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'job_host'">{{ record.job_host_name || '—' }}</template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="execStatusMeta[record.status as execApi.ExecutionStatus]?.color">
            {{ execStatusMeta[record.status as execApi.ExecutionStatus]?.text || record.status }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'scale'">
          {{ record.total_steps }} 步骤
        </template>
        <template v-else-if="column.key === 'started'">{{ fmtTime(record.started_at) }}</template>
        <template v-else-if="column.key === 'finished'">{{ fmtTime(record.finished_at) }}</template>
        <template v-else-if="column.key === 'action'">
          <a-button size="small" class="op-btn-cyan" @click="openDetail(record as execApi.ExecutionBrief)">
            <EyeOutlined />
            详情
          </a-button>
        </template>
      </template>
    </a-table>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}
.kw {
  width: 220px;
}
.status-sel {
  width: 120px;
}
.range {
  width: 400px;
}
</style>
