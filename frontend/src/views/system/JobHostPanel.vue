<script setup lang="ts">
// 作业主机配置面板（嵌入系统设置页）：分页列表 + 创建/编辑抽屉（登录认证随关联凭据，主机侧只选不填）
// + 连通性测试 / 启用禁用 / 删除（job_host:read / job_host:write）
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { ApiOutlined, DeleteOutlined, EditOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons-vue'
import * as jobHostApi from '@/api/jobHost'
import * as jobApi from '@/api/job'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const canWrite = userStore.hasPerm('job_host:write')
const canDelete = userStore.hasPerm('job_host:delete')

const enabledOptions = [
  { label: '已启用', value: 'true' },
  { label: '已禁用', value: 'false' },
]

const DEFAULT_WORKDIR = '/opt/opspilot/workspace'

// ---------- 列表 ----------
const loading = ref(false)
const items = ref<jobHostApi.JobHost[]>([])
const total = ref(0)
const query = reactive({
  page: 1,
  page_size: 20,
  keyword: '',
  enabled: undefined as string | undefined,
})

// 操作列固定右侧，其余列可拖拽调宽（响应式包装使 width 变更生效）
const columns = ref(makeResizable([
  { title: '名称', dataIndex: 'name', key: 'name', width: 140, ellipsis: true },
  { title: 'IP 地址', dataIndex: 'ip', key: 'ip', width: 130 },
  { title: 'SSH 端口', dataIndex: 'ssh_port', key: 'ssh_port', width: 90 },
  { title: '关联凭据', key: 'credential', width: 160, ellipsis: true },
  { title: '工作目录', dataIndex: 'workdir', key: 'workdir', width: 190, ellipsis: true },
  { title: '启用状态', key: 'enabled', width: 95 },
  { title: '最近测试', key: 'last_check', width: 180 },
  { title: '操作', key: 'action', width: canWrite || canDelete ? 200 : 60, fixed: 'right' as const },
]))

/** 拉取作业主机列表（响应仅含 credential_name，无任何密文字段） */
async function loadList() {
  loading.value = true
  try {
    const data = await jobHostApi.listJobHosts({
      page: query.page,
      page_size: query.page_size,
      keyword: query.keyword || undefined,
      enabled: query.enabled === undefined ? undefined : query.enabled === 'true',
    })
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
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

// ---------- 创建 / 编辑（抽屉） ----------
const editVisible = ref(false)
const editLoading = ref(false)
const editing = ref<jobHostApi.JobHost | null>(null) // null=创建
const editForm = reactive({
  name: '',
  ip: '',
  ssh_port: 22,
  credential_id: undefined as number | undefined,
  workdir: DEFAULT_WORKDIR,
})

// 凭据选项：凭据管理中的凭据（登录认证随凭据走，主机侧只选不填）
const credOptions = ref<{ label: string; value: number }[]>([])
async function loadCredOptions() {
  const [passwords, privateKeys] = await Promise.all([
    jobApi.listCredentials({ page: 1, page_size: 100, auth_type: 'password' }),
    jobApi.listCredentials({ page: 1, page_size: 100, auth_type: 'private_key' }),
  ])
  credOptions.value = [...passwords.items, ...privateKeys.items].map((c) => ({
    value: c.id,
    label: `${c.name}（${c.login_user || '—'} · ${c.auth_type === 'password' ? 'SSH 密码' : 'SSH 私钥'}）`,
  }))
}

function openCreate() {
  editing.value = null
  Object.assign(editForm, {
    name: '',
    ip: '',
    ssh_port: 22,
    credential_id: undefined,
    workdir: DEFAULT_WORKDIR,
  })
  loadCredOptions()
  editVisible.value = true
}

/** 编辑：回填基础信息与关联凭据 */
function openEdit(row: jobHostApi.JobHost) {
  editing.value = row
  Object.assign(editForm, {
    name: row.name,
    ip: row.ip,
    ssh_port: row.ssh_port,
    credential_id: row.credential_id ?? undefined,
    workdir: row.workdir,
  })
  loadCredOptions()
  editVisible.value = true
}

/** 提交创建/编辑（登录账号与密文随关联凭据，主机侧不再填写） */
async function onSubmitEdit() {
  if (!editForm.name || !editForm.ip) {
    message.warning('请填写名称与 IP 地址')
    return
  }
  if (!editForm.credential_id) {
    message.warning('请选择关联凭据')
    return
  }
  editLoading.value = true
  try {
    if (editing.value) {
      const payload: jobHostApi.JobHostUpdate = {
        name: editForm.name,
        ip: editForm.ip,
        ssh_port: editForm.ssh_port,
        credential_id: editForm.credential_id,
        workdir: editForm.workdir || DEFAULT_WORKDIR,
      }
      await jobHostApi.updateJobHost(editing.value.id, payload)
      message.success('已保存')
    } else {
      const payload: jobHostApi.JobHostCreate = {
        name: editForm.name,
        ip: editForm.ip,
        ssh_port: editForm.ssh_port,
        credential_id: editForm.credential_id,
        workdir: editForm.workdir || DEFAULT_WORKDIR,
      }
      await jobHostApi.createJobHost(payload)
      message.success('作业主机已创建')
    }
    editVisible.value = false
    loadList()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    editLoading.value = false
  }
}

// ---------- 连通性测试 / 启用禁用 / 删除 ----------
const testingId = ref<number | null>(null)

/** 连通性测试：SSH 建连执行 echo ok，结果落 last_check_* 后刷新列表展示 */
async function onTest(row: jobHostApi.JobHost) {
  testingId.value = row.id
  try {
    const result = await jobHostApi.testJobHost(row.id)
    if (result.ok) message.success(`连通性测试成功：${result.message}`)
    else message.error(`连通性测试失败：${result.message}`)
    loadList()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    testingId.value = null
  }
}

const switchingId = ref<number | null>(null)

/** 启用/禁用切换：禁用后不可被新模板选用 */
async function onToggleEnabled(row: jobHostApi.JobHost, checked: boolean) {
  switchingId.value = row.id
  try {
    await jobHostApi.setJobHostStatus(row.id, checked)
    message.success(checked ? '已启用' : '已禁用')
    loadList()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    switchingId.value = null
  }
}

/** 删除作业主机（被工单模板引用时后端 42201 删除保护，拦截器提示原因） */
async function onDelete(row: jobHostApi.JobHost) {
  try {
    await jobHostApi.deleteJobHost(row.id)
    message.success('已删除')
    loadList()
  } catch {
    /* 42201 删除保护等提示由拦截器统一弹出 */
  }
}

onMounted(loadList)
</script>

<template>
  <div>
    <!-- 工具栏 -->
    <div class="toolbar">
      <a-input
        v-model:value="query.keyword"
        placeholder="搜索名称 / IP"
        class="kw"
        allow-clear
        @press-enter="onSearch"
      >
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-select
        v-model:value="query.enabled"
        placeholder="启用状态"
        class="enabled-sel"
        allow-clear
        :options="enabledOptions"
        @change="onSearch"
      />
      <div v-if="canWrite" class="toolbar-actions">
        <a-button type="primary" @click="openCreate"><PlusOutlined />新建作业主机</a-button>
      </div>
    </div>

    <!-- 列表：列间分割线 + 操作列固定 + 其余列可拖拽调宽 -->
    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      row-key="id"
      bordered
      size="small"
      :scroll="{ x: 1240 }"
      @resize-column="onResizeColumn"
      :pagination="{
        current: query.page,
        pageSize: query.page_size,
        total,
        showSizeChanger: true,
        showQuickJumper: true,
        showTotal: (t: number) => `共 ${t} 台作业主机`,
        onChange: onPageChange,
      }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'credential'">
          <span v-if="record.credential_name">{{ record.credential_name }}</span>
          <a-tag v-else color="orange">未关联凭据</a-tag>
        </template>
        <template v-else-if="column.key === 'enabled'">
          <a-switch
            :checked="record.enabled"
            :disabled="!canWrite"
            :loading="switchingId === record.id"
            checked-children="启用"
            un-checked-children="禁用"
            @change="(checked: boolean) => onToggleEnabled(record as jobHostApi.JobHost, checked)"
          />
        </template>
        <template v-else-if="column.key === 'last_check'">
          <template v-if="record.last_check_at">
            <a-tooltip :title="record.last_check_msg || undefined">
              <a-tag :color="record.last_check_ok ? 'success' : 'error'">
                {{ record.last_check_ok ? '成功' : '失败' }}
              </a-tag>
              <span class="check-time">{{ new Date(record.last_check_at).toLocaleString() }}</span>
            </a-tooltip>
          </template>
          <template v-else>—</template>
        </template>
        <template v-else-if="column.key === 'action'">
          <a-space v-if="canWrite || canDelete">
            <a-button
              v-if="canWrite"
              size="small"
              class="op-btn-cyan"
              :loading="testingId === record.id"
              @click="onTest(record as jobHostApi.JobHost)"
            ><ApiOutlined />测试</a-button>
            <a-button v-if="canWrite" size="small" class="op-btn-blue" @click="openEdit(record as jobHostApi.JobHost)"><EditOutlined />编辑</a-button>
            <a-popconfirm v-if="canDelete" title="确认删除该作业主机？" @confirm="onDelete(record as jobHostApi.JobHost)">
              <a-button size="small" danger><DeleteOutlined />删除</a-button>
            </a-popconfirm>
          </a-space>
          <template v-else>—</template>
        </template>
      </template>
    </a-table>

    <!-- 创建/编辑抽屉：登录认证随关联凭据，主机侧只选凭据 -->
    <a-drawer
      v-model:open="editVisible"
      :title="editing ? '编辑作业主机' : '新建作业主机'"
      :width="520"
    >
      <a-form layout="vertical">
        <a-form-item label="名称" required>
          <a-input v-model:value="editForm.name" placeholder="如 默认作业主机" />
        </a-form-item>
        <div class="form-row">
          <a-form-item label="IP 地址" required class="form-col">
            <a-input v-model:value="editForm.ip" placeholder="如 10.0.0.10" />
          </a-form-item>
          <a-form-item label="SSH 端口" class="form-port">
            <a-input-number v-model:value="editForm.ssh_port" :min="1" :max="65535" style="width: 100%" />
          </a-form-item>
        </div>
        <a-form-item label="关联凭据" required>
          <a-select
            v-model:value="editForm.credential_id"
            :options="credOptions"
            show-search
            option-filter-prop="label"
            placeholder="选择凭据管理中的凭据"
          />
          <div class="form-tip">登录账号与密文随凭据管理维护；如需新增请先到 作业中心 → 凭据管理 创建</div>
        </a-form-item>
        <a-form-item label="工作目录">
          <a-input v-model:value="editForm.workdir" :placeholder="DEFAULT_WORKDIR" />
          <div class="form-tip">脚本落盘与执行的根目录，需登录用户有读写权限</div>
        </a-form-item>
      </a-form>
      <template #footer>
        <a-space class="drawer-footer">
          <a-button @click="editVisible = false">取消</a-button>
          <a-button type="primary" :loading="editLoading" @click="onSubmitEdit">
            {{ editing ? '保存' : '创建' }}
          </a-button>
        </a-space>
      </template>
    </a-drawer>
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
.enabled-sel {
  width: 120px;
}
.toolbar-actions {
  margin-left: auto;
  display: flex;
  gap: 10px;
}
.check-time {
  margin-left: 6px;
  font-size: 12px;
  color: var(--text-3);
}
.form-row {
  display: flex;
  gap: 16px;
}
.form-col {
  flex: 1;
}
.form-port {
  width: 130px;
}
.form-tip {
  font-size: 12px;
  color: var(--text-3);
  margin-top: 6px;
}
.drawer-footer {
  display: flex;
  justify-content: flex-end;
  width: 100%;
}
</style>
