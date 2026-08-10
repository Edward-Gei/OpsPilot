<script setup lang="ts">
// 待办审批：当前登录人所属角色处于当前审批节点的工单清单（FLOW-06）
// 审批操作复用 TicketDetailDrawer（showApprove=true），审批后刷新列表
import { onMounted, reactive, ref } from 'vue'
import { AuditOutlined } from '@ant-design/icons-vue'
import * as ticketApi from '@/api/ticket'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { fmtTime } from './meta'
import TicketDetailDrawer from './TicketDetailDrawer.vue'

// ---------- 列表 ----------
const loading = ref(false)
const items = ref<ticketApi.TicketBrief[]>([])
const total = ref(0)
const query = reactive({ page: 1, page_size: 20 })

const columns = ref(makeResizable([
  { title: '工单号', dataIndex: 'ticket_no', key: 'ticket_no', width: 150 },
  { title: '标题（模板名）', dataIndex: 'title', key: 'title', width: 220, ellipsis: true },
  { title: '作业主机', key: 'job_host', width: 150, ellipsis: true },
  { title: '当前节点', key: 'node', width: 110 },
  { title: '提交人', dataIndex: 'creator_name', key: 'creator_name', width: 110, ellipsis: true },
  { title: '提交时间', key: 'submitted', width: 155 },
  { title: '操作', key: 'action', width: 90, fixed: 'right' as const },
]))

/** 拉取待我审批列表（后端按角色与当前节点过滤） */
async function loadList() {
  loading.value = true
  try {
    const data = await ticketApi.todoTickets({ page: query.page, page_size: query.page_size })
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function onPageChange(page: number, pageSize: number) {
  query.page = page
  query.page_size = pageSize
  loadList()
}

// ---------- 审批抽屉 ----------
const detailOpen = ref(false)
const detailId = ref<number | null>(null)

function openApprove(row: ticketApi.TicketBrief) {
  detailId.value = row.id
  detailOpen.value = true
}

onMounted(loadList)
</script>

<template>
  <div>
    <!-- 彩色横幅（与 M1 页面同构） -->
    <div class="op-hero op-hero--amber">
      <div class="op-hero-icon"><AuditOutlined /></div>
      <div>
        <div class="op-hero-title">待办审批</div>
        <div class="op-hero-sub">当前审批节点轮到您所属角色处理的工单</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat"><b>{{ total }}</b><span>待处理</span></div>
      </div>
    </div>

    <!-- 待办列表 -->
    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      row-key="id"
      bordered
      :scroll="{ x: 980 }"
      @resize-column="onResizeColumn"
      :pagination="{
        current: query.page,
        pageSize: query.page_size,
        total,
        showSizeChanger: true,
        showQuickJumper: true,
        showTotal: (t: number) => `共 ${t} 个待办`,
        onChange: onPageChange,
      }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'job_host'">{{ record.job_host_name || '—' }}</template>
        <template v-else-if="column.key === 'node'">
          <a-tag color="gold">节点 {{ record.current_node }}/{{ record.total_nodes }}</a-tag>
        </template>
        <template v-else-if="column.key === 'submitted'">{{ fmtTime(record.submitted_at) }}</template>
        <template v-else-if="column.key === 'action'">
          <a-button size="small" class="op-btn-green" @click="openApprove(record as ticketApi.TicketBrief)">
            <AuditOutlined />
            审批
          </a-button>
        </template>
      </template>
    </a-table>

    <!-- 审批抽屉：详情 + 通过/驳回操作区 -->
    <TicketDetailDrawer v-model:open="detailOpen" :ticket-id="detailId" show-approve @changed="loadList" />
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}
</style>
