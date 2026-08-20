<script setup lang="ts">
// 主机管理：分页列表 + 多条件筛选（platform/region 自动补全）+ 创建/编辑/删除/批量删除
// + Excel 模板下载/导入（失败行明细）/按筛选导出 + 详情抽屉反查关联应用（cmdb:read / cmdb:write）
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  CloudServerOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  EyeOutlined,
  PlusOutlined,
  SearchOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue'
import * as cmdbApi from '@/api/cmdb'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const canWrite = userStore.hasPerm('cmdb:write')
const canDelete = userStore.hasPerm('cmdb:delete')
const canImport = userStore.hasPerm('cmdb:import')

// 环境/状态彩色标签（与 M1 列表标签风格一致；环境按产品约定保留英文原值）
const envText: Record<string, { text: string; color: string }> = {
  demo: { text: 'demo', color: 'green' },
  stage: { text: 'stage', color: 'orange' },
  prod: { text: 'prod', color: 'red' },
}
const statusText: Record<string, { text: string; color: string }> = {
  online: { text: '在线', color: 'success' },
  offline: { text: '离线', color: 'default' },
  maintenance: { text: '维护', color: 'warning' },
}
const envOptions = Object.entries(envText).map(([value, v]) => ({ label: v.text, value }))
const statusOptions = Object.entries(statusText).map(([value, v]) => ({ label: v.text, value }))

/** 资源配置合并展示：如「4C / 8G / 100G」，未录入部分用 ? 占位，全空显示 — */
function specText(row: { cpu_cores: number | null; memory_gb: number | null; disk_gb: number | null }) {
  if (row.cpu_cores == null && row.memory_gb == null && row.disk_gb == null) return '—'
  const part = (v: number | null, unit: string) => (v == null ? '?' : `${v}${unit}`)
  return `${part(row.cpu_cores, 'C')} / ${part(row.memory_gb, 'G')} / ${part(row.disk_gb, 'G')}`
}

// ---------- 列表 ----------
const loading = ref(false)
const items = ref<cmdbApi.HostItem[]>([])
const total = ref(0)
const route = useRoute()
const router = useRouter()

const query = reactive({
  page: 1,
  page_size: 20,
  keyword: '',
  platform: undefined as string | undefined,
  region: undefined as string | undefined,
  environment: undefined as string | undefined,
  status: undefined as string | undefined,
  sort_by: undefined as 'created_at' | undefined,
  sort_order: undefined as 'asc' | 'desc' | undefined,
})

// 列宽尽量均匀；操作列固定右侧，其余列可拖拽调宽（响应式包装使 width 变更生效）
const columns = ref(makeResizable([
  { title: '主机名', dataIndex: 'hostname', key: 'hostname', width: 130, ellipsis: true },
  { title: '内网 IP 地址', dataIndex: 'ip', key: 'ip', width: 120 },
  { title: '公网 IP 地址', key: 'public_ip', width: 120 },
  { title: '项目', dataIndex: 'project', key: 'project', width: 100 },
  { title: 'RI', key: 'ri', width: 110, ellipsis: true },
  { title: '主机系列', key: 'host_series', width: 110, ellipsis: true },
  { title: '平台', key: 'platform', width: 100, ellipsis: true },
  { title: '区域', key: 'region', width: 100, ellipsis: true },
  { title: '操作系统', key: 'os', width: 110, ellipsis: true },
  { title: '资源配置', key: 'spec', width: 120 },
  { title: '环境', key: 'environment', width: 90 },
  { title: '状态', key: 'status', width: 90 },
  { title: 'SSH 端口', dataIndex: 'ssh_port', key: 'ssh_port', width: 90 },
  { title: '创建时间', dataIndex: 'created_at', key: 'created', width: 155, sorter: true },
  { title: '操作', key: 'action', width: canWrite || canDelete ? 190 : 90, fixed: 'right' as const },
]))

/** 拉取主机列表（携带全部筛选条件） */
async function loadList() {
  loading.value = true
  try {
    const data = await cmdbApi.listHosts({
      page: query.page,
      page_size: query.page_size,
      keyword: query.keyword || undefined,
      platform: query.platform || undefined,
      region: query.region || undefined,
      environment: query.environment,
      status: query.status,
      sort_by: query.sort_by,
      sort_order: query.sort_order,
    })
    items.value = data.items
    total.value = data.total
    selectedKeys.value = []
  } finally {
    loading.value = false
  }
}

// ---------- 批量删除（多选；单台删除保护失败不影响其余） ----------
const selectedKeys = ref<number[]>([])
const rowSelection = computed(() =>
  canWrite
    ? {
        fixed: true, // 选择框列固定左侧
        selectedRowKeys: selectedKeys.value,
        onChange: (keys: (string | number)[]) => (selectedKeys.value = keys as number[]),
      }
    : undefined,
)
const batchLoading = ref(false)

/** 批量删除：逐台调删除接口，被引用（42201 删除保护）的失败计数，原因由拦截器逐条提示 */
async function onBatchDelete() {
  if (!selectedKeys.value.length) return
  batchLoading.value = true
  try {
    const results = await Promise.allSettled(
      selectedKeys.value.map((id) => cmdbApi.deleteHost(id)),
    )
    const okCount = results.filter((r) => r.status === 'fulfilled').length
    if (okCount < results.length) message.warning(`已删除 ${okCount} 台，${results.length - okCount} 台失败（被引用等）`)
    else message.success(`已删除 ${okCount} 台主机`)
    refreshAll()
  } finally {
    batchLoading.value = false
  }
}

// ---------- 横幅统计（全局口径，不受筛选影响，page_size=1 只取 total） ----------
const stats = reactive({ all: 0, online: 0, prod: 0 })

async function loadStats() {
  const [all, online, prod] = await Promise.all([
    cmdbApi.listHosts({ page: 1, page_size: 1 }),
    cmdbApi.listHosts({ page: 1, page_size: 1, status: 'online' }),
    cmdbApi.listHosts({ page: 1, page_size: 1, environment: 'prod' }),
  ])
  stats.all = all.total
  stats.online = online.total
  stats.prod = prod.total
}

/** 列表 + 统计一起刷新（变更操作后调用） */
function refreshAll() {
  loadList()
  loadStats()
}

function onSearch() {
  query.page = 1
  loadList()
}

function onTableChange(
  pagination: { current?: number; pageSize?: number },
  _: unknown,
  sorter: { field?: string; order?: 'ascend' | 'descend' | null } | { field?: string; order?: 'ascend' | 'descend' | null }[],
) {
  const current = Array.isArray(sorter) ? sorter[0] : sorter
  const sortBy = current.order && current.field === 'created_at' ? 'created_at' : undefined
  const sortOrder = current.order === 'ascend' ? 'asc' : current.order === 'descend' ? 'desc' : undefined
  const sortChanged = query.sort_by !== sortBy || query.sort_order !== sortOrder
  query.sort_by = sortBy
  query.sort_order = sortOrder
  query.page = sortChanged ? 1 : pagination.current || 1
  query.page_size = pagination.pageSize || query.page_size
  loadList()
}

// ---------- platform/region 自动补全（复用后端 suggest 接口，筛选与表单共用） ----------
const platformOptions = ref<{ value: string }[]>([])
const regionOptions = ref<{ value: string }[]>([])
const hostSeriesOptions = ref<{ value: string }[]>([])
const projectOptions = [
  { value: 'mitrade', label: 'mitrade' },
  { value: 'tradingkey', label: 'tradingkey' },
]

/** 拉取自由文本补全项：q 为已输入前缀。 */
async function loadSuggest(field: 'platform' | 'region' | 'host_series', q?: string) {
  const data = await cmdbApi.suggestHostField(field, q || undefined)
  const opts = data.items.map((v) => ({ value: v }))
  if (field === 'platform') platformOptions.value = opts
  else if (field === 'region') regionOptions.value = opts
  else hostSeriesOptions.value = opts
}

// ---------- 创建 / 编辑 ----------
const editVisible = ref(false)
const editLoading = ref(false)
const editing = ref<cmdbApi.HostItem | null>(null) // null=创建
const editForm = reactive<cmdbApi.HostForm>({
  hostname: '',
  ip: '',
  project: 'mitrade',
  public_ip: '',
  ri: '',
  host_series: '',
  platform: '',
  region: '',
  os: '',
  cpu_cores: null,
  memory_gb: null,
  disk_gb: null,
  environment: 'prod',
  status: 'online',
  ssh_port: 22,
  description: '',
})

function openCreate() {
  editing.value = null
  Object.assign(editForm, {
    hostname: '', ip: '', project: 'mitrade', public_ip: '', ri: '', host_series: '', platform: '', region: '', os: '',
    cpu_cores: null, memory_gb: null, disk_gb: null,
    environment: 'prod', status: 'online', ssh_port: 22, description: '',
  })
  editVisible.value = true
}

function openEdit(row: cmdbApi.HostItem) {
  editing.value = row
  Object.assign(editForm, {
    hostname: row.hostname,
    ip: row.ip,
    project: row.project,
    public_ip: row.public_ip || '',
    ri: row.ri || '',
    host_series: row.host_series || '',
    platform: row.platform || '',
    region: row.region || '',
    os: row.os || '',
    cpu_cores: row.cpu_cores,
    memory_gb: row.memory_gb,
    disk_gb: row.disk_gb,
    environment: row.environment,
    status: row.status,
    ssh_port: row.ssh_port,
    description: row.description || '',
  })
  editVisible.value = true
}

/** 提交创建/编辑（内网 IP 冲突 40901 由拦截器统一弹出提示） */
async function onSubmitEdit() {
  if (!editForm.hostname || !editForm.ip) {
    message.warning('请填写主机名与内网 IP 地址')
    return
  }
  editLoading.value = true
  try {
    const payload = {
      ...editForm,
      public_ip: editForm.public_ip || undefined,
      ri: editForm.ri || undefined,
      host_series: editForm.host_series || undefined,
      platform: editForm.platform || undefined,
      region: editForm.region || undefined,
      os: editForm.os || undefined,
      cpu_cores: editForm.cpu_cores ?? undefined,
      memory_gb: editForm.memory_gb ?? undefined,
      disk_gb: editForm.disk_gb ?? undefined,
      description: editForm.description || undefined,
    }
    if (editing.value) {
      await cmdbApi.updateHost(editing.value.id, payload)
      message.success('已保存')
    } else {
      await cmdbApi.createHost(payload)
      message.success('主机已创建')
    }
    editVisible.value = false
    refreshAll()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    editLoading.value = false
  }
}

/** 删除主机（被应用/进行中工单引用时后端 42201 删除保护，拦截器提示原因） */
async function onDelete(row: cmdbApi.HostItem) {
  try {
    await cmdbApi.deleteHost(row.id)
    message.success('已删除')
    refreshAll()
  } catch {
    /* 42201 删除保护等提示由拦截器统一弹出 */
  }
}

// ---------- Excel 导入 ----------
const importVisible = ref(false)
const importLoading = ref(false)
const importFile = ref<File | null>(null)
const importUpsert = ref(false)
const importResult = ref<cmdbApi.ImportResult | null>(null)

function openImport() {
  importFile.value = null
  importUpsert.value = false
  importResult.value = null
  importVisible.value = true
}

/** a-upload 拦截：只留存文件不自动上传（return false） */
function onBeforeUpload(file: File) {
  importFile.value = file
  importResult.value = null
  return false
}

/** 提交导入：展示成功数与失败行明细（行号+原因），成功后刷新列表 */
async function onSubmitImport() {
  if (!importFile.value) {
    message.warning('请选择 xlsx 文件')
    return
  }
  importLoading.value = true
  try {
    const result = await cmdbApi.importHosts(importFile.value, importUpsert.value)
    importResult.value = result
    if (result.failed_rows.length === 0) {
      message.success(`导入成功 ${result.success_count} 条`)
    } else {
      message.warning(`成功 ${result.success_count} 条，失败 ${result.failed_rows.length} 条，请查看明细`)
    }
    refreshAll()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    importLoading.value = false
  }
}

/** 按当前筛选条件导出主机 xlsx */
async function onExport() {
  try {
    await cmdbApi.exportHosts({
      keyword: query.keyword || undefined,
      platform: query.platform || undefined,
      region: query.region || undefined,
      environment: query.environment,
      status: query.status,
    })
  } catch {
    message.error('导出失败')
  }
}

// ---------- 详情抽屉（反查关联应用） ----------
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<cmdbApi.HostDetail | null>(null)

/** 打开详情：拉取主机完整信息 + 关联应用清单 */
async function openDetail(row: cmdbApi.HostItem) {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await cmdbApi.getHost(row.id)
  } finally {
    detailLoading.value = false
  }
}

onMounted(() => {
  // 全局搜索跳转：?keyword= 带入搜索框自动过滤，随后清掉 query 避免刷新残留（SEARCH-04）
  const qkw = route.query.keyword
  if (typeof qkw === 'string' && qkw) {
    query.keyword = qkw
    router.replace({ path: route.path })
  }
  refreshAll()
})
</script>

<template>
  <div>
    <!-- 彩色横幅：页面标识 + 全局统计（与 M1 用户管理页同构） -->
    <div class="op-hero op-hero--cyan">
      <div class="op-hero-icon"><CloudServerOutlined /></div>
      <div>
        <div class="op-hero-title">主机管理</div>
        <div class="op-hero-sub">主机台账维护、Excel 批量导入导出与应用关联视图</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat"><b>{{ stats.all }}</b><span>总主机</span></div>
        <div class="op-hero-stat"><b>{{ stats.online }}</b><span>在线</span></div>
        <div class="op-hero-stat"><b>{{ stats.prod }}</b><span>生产环境</span></div>
      </div>
    </div>

    <!-- 工具栏：关键字 + 平台/区域自动补全 + 环境/状态下拉 -->
    <div class="toolbar">
      <a-input
        v-model:value="query.keyword"
        placeholder="搜索主机名 / IP"
        class="kw"
        allow-clear
        @press-enter="onSearch"
      >
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-auto-complete
        v-model:value="query.platform"
        placeholder="平台"
        class="ac-sel"
        allow-clear
        :options="platformOptions"
        @focus="loadSuggest('platform')"
        @search="(q: string) => loadSuggest('platform', q)"
        @change="onSearch"
      />
      <a-auto-complete
        v-model:value="query.region"
        placeholder="区域"
        class="ac-sel"
        allow-clear
        :options="regionOptions"
        @focus="loadSuggest('region')"
        @search="(q: string) => loadSuggest('region', q)"
        @change="onSearch"
      />
      <a-select
        v-model:value="query.environment"
        placeholder="环境"
        class="env-sel"
        allow-clear
        :options="envOptions"
        @change="onSearch"
      />
      <a-select
        v-model:value="query.status"
        placeholder="状态"
        class="env-sel"
        allow-clear
        :options="statusOptions"
        @change="onSearch"
      />
      <!-- 右侧操作组：批量删除 → 导出 → 导入 → 新建 -->
      <div class="toolbar-actions">
        <a-popconfirm
          v-if="canDelete"
          :title="`确认删除选中的 ${selectedKeys.length} 台主机？`"
          :disabled="!selectedKeys.length"
          @confirm="onBatchDelete"
        >
          <a-button danger :disabled="!selectedKeys.length" :loading="batchLoading">
            <DeleteOutlined />批量删除{{ selectedKeys.length ? `（${selectedKeys.length}）` : '' }}
          </a-button>
        </a-popconfirm>
        <a-button @click="onExport"><DownloadOutlined />导出</a-button>
        <template v-if="canImport || canWrite">
          <a-button v-if="canImport" @click="openImport"><UploadOutlined />导入</a-button>
          <a-button v-if="canWrite" type="primary" @click="openCreate"><PlusOutlined />新建主机</a-button>
        </template>
      </div>
    </div>

    <!-- 列表：列间分割线 + 选择框/操作列固定 + 其余列可拖拽调宽 -->
    <a-table
      :columns="columns"
      :data-source="items"
      :show-sorter-tooltip="false"
      :loading="loading"
      row-key="id"
      bordered
      :scroll="{ x: 1780 }"
      :row-selection="rowSelection"
      @resize-column="onResizeColumn"
      @change="onTableChange"
      :pagination="{
        current: query.page,
        pageSize: query.page_size,
        total,
        showSizeChanger: true,
        showQuickJumper: true,
        showTotal: (t: number) => `共 ${t} 台主机`,
      }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'public_ip'">{{ record.public_ip || '—' }}</template>
        <template v-else-if="column.key === 'ri'">{{ record.ri || '—' }}</template>
        <template v-else-if="column.key === 'host_series'">{{ record.host_series || '—' }}</template>
        <template v-else-if="column.key === 'platform'">{{ record.platform || '—' }}</template>
        <template v-else-if="column.key === 'region'">{{ record.region || '—' }}</template>
        <template v-else-if="column.key === 'os'">{{ record.os || '—' }}</template>
        <template v-else-if="column.key === 'spec'">{{ specText(record as cmdbApi.HostItem) }}</template>
        <template v-else-if="column.key === 'environment'">
          <a-tag :color="envText[record.environment]?.color">
            {{ envText[record.environment]?.text || record.environment }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="statusText[record.status]?.color">
            {{ statusText[record.status]?.text || record.status }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'created'">
          {{ record.created_at ? new Date(record.created_at).toLocaleString() : '—' }}
        </template>
        <template v-else-if="column.key === 'action'">
          <a-space>
            <a-button size="small" class="op-btn-cyan" @click="openDetail(record as cmdbApi.HostItem)"><EyeOutlined />详情</a-button>
            <template v-if="canWrite || canDelete">
              <a-button size="small" class="op-btn-blue" @click="openEdit(record as cmdbApi.HostItem)"><EditOutlined />编辑</a-button>
              <a-popconfirm v-if="canDelete" title="确认删除该主机？" @confirm="onDelete(record as cmdbApi.HostItem)">
                <a-button size="small" danger><DeleteOutlined />删除</a-button>
              </a-popconfirm>
            </template>
          </a-space>
        </template>
      </template>
    </a-table>

    <!-- 创建/编辑弹窗 -->
    <a-modal
      v-model:open="editVisible"
      :title="editing ? '编辑主机' : '新建主机'"
      :width="920"
      :confirm-loading="editLoading"
      @ok="onSubmitEdit"
    >
      <a-form layout="vertical" class="host-edit-form">
        <div class="host-form-grid">
          <a-form-item label="主机名" required>
            <a-input v-model:value="editForm.hostname" placeholder="如 web-prod-01" />
          </a-form-item>
          <a-form-item label="项目">
            <a-select v-model:value="editForm.project" :options="projectOptions" />
          </a-form-item>
          <a-form-item label="内网 IP 地址" required>
            <a-input v-model:value="editForm.ip" placeholder="如 10.0.0.1" />
          </a-form-item>
          <a-form-item label="公网 IP 地址">
            <a-input v-model:value="editForm.public_ip" placeholder="如 203.0.113.1" />
          </a-form-item>
          <a-form-item label="所属平台">
            <a-auto-complete
              v-model:value="editForm.platform"
              placeholder="如 阿里云（可自由输入）"
              :options="platformOptions"
              @focus="loadSuggest('platform')"
              @search="(q: string) => loadSuggest('platform', q)"
            />
          </a-form-item>
          <a-form-item label="所属区域">
            <a-auto-complete
              v-model:value="editForm.region"
              placeholder="如 华东1（可自由输入）"
              :options="regionOptions"
              @focus="loadSuggest('region')"
              @search="(q: string) => loadSuggest('region', q)"
            />
          </a-form-item>
          <a-form-item label="RI">
            <a-input v-model:value="editForm.ri" />
          </a-form-item>
          <a-form-item label="主机系列">
            <a-auto-complete
              v-model:value="editForm.host_series"
              placeholder="如 C7（可自由输入）"
              :options="hostSeriesOptions"
              @focus="loadSuggest('host_series')"
              @search="(q: string) => loadSuggest('host_series', q)"
            />
          </a-form-item>
          <a-form-item label="操作系统">
            <a-input v-model:value="editForm.os" placeholder="如 CentOS 7.9" />
          </a-form-item>
          <a-form-item label="资源配置">
            <div class="spec-inputs">
              <a-input-number v-model:value="editForm.cpu_cores" :min="1" :max="4096" placeholder="CPU 核数" addon-after="核" />
              <a-input-number v-model:value="editForm.memory_gb" :min="1" :max="65536" placeholder="内存" addon-after="GB" />
              <a-input-number v-model:value="editForm.disk_gb" :min="1" :max="1048576" placeholder="磁盘" addon-after="GB" />
            </div>
          </a-form-item>
          <a-form-item label="环境" required>
            <a-select v-model:value="editForm.environment" :options="envOptions" />
          </a-form-item>
          <a-form-item label="状态">
            <a-select v-model:value="editForm.status" :options="statusOptions" />
          </a-form-item>
          <a-form-item label="SSH 端口">
            <a-input-number v-model:value="editForm.ssh_port" :min="1" :max="65535" style="width: 100%" />
          </a-form-item>
          <a-form-item label="说明">
            <a-textarea v-model:value="editForm.description" :rows="2" />
          </a-form-item>
        </div>
      </a-form>
    </a-modal>

    <!-- Excel 导入弹窗 -->
    <a-modal
      v-model:open="importVisible"
      title="Excel 导入主机"
      :confirm-loading="importLoading"
      ok-text="开始导入"
      @ok="onSubmitImport"
    >
      <a-alert type="info" show-icon class="import-tip">
        <template #message>
          请先<a @click="cmdbApi.downloadImportTemplate()">下载导入模板</a>，按模板填写后上传（单次上限 5000 行）
        </template>
      </a-alert>
      <a-upload :before-upload="onBeforeUpload" :max-count="1" accept=".xlsx" :show-upload-list="false">
        <a-button><UploadOutlined />选择 xlsx 文件</a-button>
      </a-upload>
      <div v-if="importFile" class="import-file">已选择：{{ importFile.name }}</div>
      <a-checkbox v-model:checked="importUpsert" class="import-upsert">
        IP 已存在时更新该主机（不勾选则报“IP 已存在”）
      </a-checkbox>

      <!-- 导入结果：成功数 + 失败行明细 -->
      <template v-if="importResult">
        <a-divider class="import-divider" />
        <div class="import-summary">
          成功 <b class="ok">{{ importResult.success_count }}</b> 条，失败
          <b class="fail">{{ importResult.failed_rows.length }}</b> 条
        </div>
        <a-table
          v-if="importResult.failed_rows.length"
          bordered
          :columns="[
            { title: '行号', dataIndex: 'row', width: 70 },
            { title: '失败原因', dataIndex: 'reason' },
          ]"
          :data-source="importResult.failed_rows"
          row-key="row"
          size="small"
          :pagination="false"
          :scroll="{ y: 200 }"
        />
      </template>
    </a-modal>

    <!-- 详情抽屉：主机信息 + 反查关联应用 -->
    <a-drawer v-model:open="detailVisible" :title="detail?.hostname || '主机详情'" :width="600">
      <a-spin :spinning="detailLoading">
        <template v-if="detail">
          <a-descriptions :column="1" bordered size="small" class="op-desc-table">
            <a-descriptions-item label="主机名">{{ detail.hostname }}</a-descriptions-item>
            <a-descriptions-item label="内网 IP 地址">{{ detail.ip }}</a-descriptions-item>
            <a-descriptions-item label="公网 IP 地址">{{ detail.public_ip || '—' }}</a-descriptions-item>
            <a-descriptions-item label="项目">{{ detail.project }}</a-descriptions-item>
            <a-descriptions-item label="RI">{{ detail.ri || '—' }}</a-descriptions-item>
            <a-descriptions-item label="主机系列">{{ detail.host_series || '—' }}</a-descriptions-item>
            <a-descriptions-item label="所属平台">{{ detail.platform || '—' }}</a-descriptions-item>
            <a-descriptions-item label="所属区域">{{ detail.region || '—' }}</a-descriptions-item>
            <a-descriptions-item label="操作系统">{{ detail.os || '—' }}</a-descriptions-item>
            <a-descriptions-item label="资源配置">{{ specText(detail) }}</a-descriptions-item>
            <a-descriptions-item label="环境">
              <a-tag :color="envText[detail.environment]?.color">
                {{ envText[detail.environment]?.text || detail.environment }}
              </a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="状态">
              <a-tag :color="statusText[detail.status]?.color">
                {{ statusText[detail.status]?.text || detail.status }}
              </a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="SSH 端口">{{ detail.ssh_port }}</a-descriptions-item>
            <a-descriptions-item label="说明">{{ detail.description || '—' }}</a-descriptions-item>
            <a-descriptions-item label="创建时间">
              {{ detail.created_at ? new Date(detail.created_at).toLocaleString() : '—' }}
            </a-descriptions-item>
          </a-descriptions>

          <div class="detail-apps-title">关联应用（{{ detail.apps.length }}）</div>
          <a-empty v-if="!detail.apps.length" description="暂无关联应用" />
          <a-table
            v-else
            bordered
            :columns="[
              { title: '应用名', dataIndex: 'name' },
              { title: '语言', dataIndex: 'language', width: 90 },
              { title: '部署方式', dataIndex: 'deploy_type', width: 90 },
            ]"
            :data-source="detail.apps"
            row-key="id"
            size="small"
            :pagination="false"
          />
        </template>
      </a-spin>
    </a-drawer>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.kw {
  width: 220px;
}
.ac-sel {
  width: 130px;
}
.env-sel {
  width: 100px;
}
.toolbar-actions {
  margin-left: auto;
  display: flex;
  gap: 10px;
}
.spec-inputs {
  display: flex;
  gap: 8px;
}
.spec-inputs :deep(.ant-input-number) {
  flex: 1;
}
.host-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}
.host-form-grid :deep(.ant-form-item) {
  margin-bottom: 18px;
}
@media (max-width: 768px) {
  .host-form-grid {
    grid-template-columns: 1fr;
  }
}
.import-tip {
  margin-bottom: 14px;
}
.import-file {
  margin-top: 8px;
  font-size: 13px;
  color: var(--text-2, #555);
}
.import-upsert {
  display: block;
  margin-top: 12px;
}
.import-divider {
  margin: 14px 0 10px;
}
.import-summary {
  margin-bottom: 8px;
}
.import-summary .ok {
  color: #16a34a;
}
.import-summary .fail {
  color: #dc2626;
}
.detail-apps-title {
  font-weight: 600;
  margin: 18px 0 10px;
}
</style>
