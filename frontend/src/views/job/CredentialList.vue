<script setup lang="ts">
// 凭据管理：分页列表 + 创建/编辑（密文任何接口不回显，编辑留空=不变更）+ 删除/批量删除（credential:read / credential:write）
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  DeleteOutlined,
  EditOutlined,
  KeyOutlined,
  PlusOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue'
import * as jobApi from '@/api/job'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const canCredentialWrite = userStore.hasPerm('credential:write')
const canSecretWrite = userStore.hasPerm('secret:write')
const canCredentialDelete = userStore.hasPerm('credential:delete')
const canSecretDelete = userStore.hasPerm('secret:delete')
const canWrite = canCredentialWrite || canSecretWrite
const canDelete = canCredentialDelete || canSecretDelete

// 认证方式彩色标签（与 M1/M2 列表标签风格一致）
const authText: Record<string, { text: string; color: string }> = {
  password: { text: 'SSH 密码', color: 'geekblue' },
  private_key: { text: 'SSH 私钥', color: 'purple' },
  api_token: { text: 'API Token', color: 'cyan' },
  username_password: { text: '用户名密码', color: 'blue' },
  secret_file: { text: '文本密钥文件', color: 'green' },
}
const authOptions = Object.entries(authText).map(([value, v]) => ({ label: v.text, value }))
const editableAuthOptions = computed(() => authOptions.filter((item) =>
  ['password', 'private_key'].includes(item.value) ? canCredentialWrite : canSecretWrite,
))
const isSshCredential = (type: string) => type === 'password' || type === 'private_key'
const needsLoginUser = (type: string) => type === 'password' || type === 'private_key' || type === 'username_password'
const canEditRow = (row: jobApi.CredentialItem) => isSshCredential(row.auth_type) ? canCredentialWrite : canSecretWrite
const canDeleteRow = (row: jobApi.CredentialItem) => isSshCredential(row.auth_type) ? canCredentialDelete : canSecretDelete

// ---------- 列表 ----------
const loading = ref(false)
const items = ref<jobApi.CredentialItem[]>([])
const total = ref(0)
const query = reactive({
  page: 1,
  page_size: 20,
  keyword: '',
  auth_type: undefined as string | undefined,
})

// 操作列固定右侧，其余列可拖拽调宽（响应式包装使 width 变更生效）
const columns = ref(makeResizable([
  { title: '凭据名', dataIndex: 'name', key: 'name', width: 150, ellipsis: true },
  { title: '登录用户', dataIndex: 'login_user', key: 'login_user', width: 120, ellipsis: true },
  { title: '凭据类型', key: 'auth_type', width: 120 },
  { title: '私钥口令', key: 'has_passphrase', width: 95 },
  { title: '文件名', key: 'file_name', width: 150, ellipsis: true },
  { title: '说明', key: 'description', width: 200, ellipsis: true },
  { title: '更新时间', key: 'updated', width: 155 },
  { title: '操作', key: 'action', width: canWrite && canDelete ? 171 : canWrite || canDelete ? 98 : 60, fixed: 'right' as const },
]))

/** 拉取凭据列表（响应仅含 has_passphrase 布尔，无任何密文字段） */
async function loadList() {
  loading.value = true
  try {
    const data = await jobApi.listCredentials({
      page: query.page,
      page_size: query.page_size,
      keyword: query.keyword || undefined,
      auth_type: query.auth_type,
    })
    items.value = data.items
    total.value = data.total
    selectedKeys.value = []
  } finally {
    loading.value = false
  }
}

// ---------- 批量删除（多选；被进行中工单引用的失败不影响其余） ----------
const selectedKeys = ref<number[]>([])
const rowSelection = computed(() =>
  canDelete
    ? {
        fixed: true, // 选择框列固定左侧
        selectedRowKeys: selectedKeys.value,
        onChange: (keys: (string | number)[]) => (selectedKeys.value = keys as number[]),
        getCheckboxProps: (row: jobApi.CredentialItem) => ({ disabled: !canDeleteRow(row) }),
      }
    : undefined,
)
const batchLoading = ref(false)

/** 批量删除：逐个调删除接口，被引用（42201 删除保护）的失败计数，原因由拦截器逐条提示 */
async function onBatchDelete() {
  if (!selectedKeys.value.length) return
  batchLoading.value = true
  try {
    const results = await Promise.allSettled(
      selectedKeys.value.map((id) => jobApi.deleteCredential(id)),
    )
    const okCount = results.filter((r) => r.status === 'fulfilled').length
    if (okCount < results.length) message.warning(`已删除 ${okCount} 个，${results.length - okCount} 个失败（被引用等）`)
    else message.success(`已删除 ${okCount} 个凭据`)
    refreshAll()
  } finally {
    batchLoading.value = false
  }
}

// ---------- 横幅统计（全局口径，不受筛选影响，page_size=1 只取 total） ----------
const stats = reactive({ all: 0, ssh: 0, secrets: 0 })

async function loadStats() {
  const [all, pwd, key, token, account, file] = await Promise.all([
    jobApi.listCredentials({ page: 1, page_size: 1 }),
    jobApi.listCredentials({ page: 1, page_size: 1, auth_type: 'password' }),
    jobApi.listCredentials({ page: 1, page_size: 1, auth_type: 'private_key' }),
    jobApi.listCredentials({ page: 1, page_size: 1, auth_type: 'api_token' }),
    jobApi.listCredentials({ page: 1, page_size: 1, auth_type: 'username_password' }),
    jobApi.listCredentials({ page: 1, page_size: 1, auth_type: 'secret_file' }),
  ])
  stats.all = all.total
  stats.ssh = pwd.total + key.total
  stats.secrets = token.total + account.total + file.total
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

function onPageChange(page: number, pageSize: number) {
  query.page = page
  query.page_size = pageSize
  loadList()
}

// ---------- 创建 / 编辑 ----------
const editVisible = ref(false)
const editLoading = ref(false)
const editing = ref<jobApi.CredentialItem | null>(null) // null=创建
const editForm = reactive({
  name: '',
  login_user: '',
  auth_type: 'password' as jobApi.CredentialItem['auth_type'],
  secret: '',
  passphrase: '',
  file_name: '',
  description: '',
})

function openCreate() {
  editing.value = null
  const authType = editableAuthOptions.value[0]?.value as jobApi.CredentialItem['auth_type'] | undefined
  Object.assign(editForm, {
    name: '',
    login_user: '',
    auth_type: authType || 'password',
    secret: '',
    passphrase: '',
    file_name: '',
    description: '',
  })
  editVisible.value = true
}

/** 编辑：回填基础信息，密文不回显（secret 留空提交 = 不变更） */
function openEdit(row: jobApi.CredentialItem) {
  editing.value = row
  Object.assign(editForm, {
    name: row.name,
    login_user: row.login_user || '',
    auth_type: row.auth_type,
    secret: '',
    passphrase: '',
    file_name: row.file_name || '',
    description: row.description || '',
  })
  editVisible.value = true
}

/**
 * 提交创建/编辑。
 * 创建必填密文；编辑 secret 留空 = 不变更密文（但变更认证方式时后端强制要求重填，前端同步拦截）
 */
async function onSubmitEdit() {
  if (!editForm.name || (needsLoginUser(editForm.auth_type) && !editForm.login_user)) {
    message.warning(needsLoginUser(editForm.auth_type) ? '请填写凭据名和登录用户' : '请填写凭据名')
    return
  }
  if (!editing.value && !editForm.secret) {
    message.warning('请填写密文内容')
    return
  }
  if (editForm.auth_type === 'secret_file' && !editForm.file_name) {
    message.warning('请填写文本密钥文件名')
    return
  }
  editLoading.value = true
  try {
    const payload: jobApi.CredentialForm = {
      name: editForm.name,
      login_user: editForm.login_user || null,
      auth_type: editForm.auth_type,
      secret: editForm.secret,
      passphrase: editForm.passphrase || undefined,
      file_name: editForm.file_name || undefined,
      description: editForm.description || undefined,
    }
    if (editing.value) {
      await jobApi.updateCredential(editing.value.id, payload)
      message.success('已保存')
    } else {
      await jobApi.createCredential(payload)
      message.success('凭据已创建')
    }
    editVisible.value = false
    refreshAll()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    editLoading.value = false
  }
}

/** 删除凭据（被进行中工单引用时后端 42201 删除保护，拦截器提示原因） */
async function onDelete(row: jobApi.CredentialItem) {
  try {
    await jobApi.deleteCredential(row.id)
    message.success('已删除')
    refreshAll()
  } catch {
    /* 42201 删除保护等提示由拦截器统一弹出 */
  }
}

onMounted(() => {
  refreshAll()
})
</script>

<template>
  <div>
    <!-- 彩色横幅：页面标识 + 全局统计（与 M1 用户管理页同构） -->
    <div class="op-hero op-hero--emerald">
      <div class="op-hero-icon"><KeyOutlined /></div>
      <div>
        <div class="op-hero-title">凭据管理</div>
        <div class="op-hero-sub">SSH 登录凭据与脚本密钥集中管理（密文加密存储，任何接口不回显）</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat"><b>{{ stats.all }}</b><span>总凭据</span></div>
        <div class="op-hero-stat"><b>{{ stats.ssh }}</b><span>SSH 凭据</span></div>
        <div class="op-hero-stat"><b>{{ stats.secrets }}</b><span>脚本密钥</span></div>
      </div>
    </div>

    <!-- 工具栏 -->
    <div class="toolbar">
      <a-input
        v-model:value="query.keyword"
        placeholder="搜索凭据名/登录用户"
        class="kw"
        allow-clear
        @press-enter="onSearch"
      >
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-select
        v-model:value="query.auth_type"
        placeholder="凭据类型"
        class="auth-sel"
        allow-clear
        :options="authOptions"
        @change="onSearch"
      />
      <!-- 右侧操作组：批量删除 + 新建整体钉右 -->
      <div v-if="canWrite || canDelete" class="toolbar-actions">
        <a-popconfirm
          v-if="canDelete"
          :title="`确认删除选中的 ${selectedKeys.length} 个凭据？`"
          :disabled="!selectedKeys.length"
          @confirm="onBatchDelete"
        >
          <a-button danger :disabled="!selectedKeys.length" :loading="batchLoading">
            <DeleteOutlined />批量删除{{ selectedKeys.length ? `（${selectedKeys.length}）` : '' }}
          </a-button>
        </a-popconfirm>
        <a-button v-if="canWrite" type="primary" @click="openCreate"><PlusOutlined />新建凭据</a-button>
      </div>
    </div>

    <!-- 列表：列间分割线 + 选择框/操作列固定 + 其余列可拖拽调宽 -->
    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      row-key="id"
      bordered
      :scroll="{ x: 1020 }"
      :row-selection="rowSelection"
      @resize-column="onResizeColumn"
      :pagination="{
        current: query.page,
        pageSize: query.page_size,
        total,
        showSizeChanger: true,
        showQuickJumper: true,
        showTotal: (t: number) => `共 ${t} 个凭据`,
        onChange: onPageChange,
      }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'auth_type'">
          <a-tag :color="authText[record.auth_type]?.color">
            {{ authText[record.auth_type]?.text || record.auth_type }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'has_passphrase'">
          <a-tag v-if="record.has_passphrase" color="cyan">有</a-tag>
          <template v-else>—</template>
        </template>
        <template v-else-if="column.key === 'file_name'">{{ record.file_name || '—' }}</template>
        <template v-else-if="column.key === 'description'">{{ record.description || '—' }}</template>
        <template v-else-if="column.key === 'updated'">
          {{ record.updated_at ? new Date(record.updated_at).toLocaleString() : '—' }}
        </template>
        <template v-else-if="column.key === 'action'">
          <a-space v-if="canEditRow(record as jobApi.CredentialItem) || canDeleteRow(record as jobApi.CredentialItem)">
            <a-button v-if="canEditRow(record as jobApi.CredentialItem)" size="small" class="op-btn-blue" @click="openEdit(record as jobApi.CredentialItem)"><EditOutlined />编辑</a-button>
            <a-popconfirm v-if="canDeleteRow(record as jobApi.CredentialItem)" title="确认删除该凭据？" @confirm="onDelete(record as jobApi.CredentialItem)">
              <a-button size="small" danger><DeleteOutlined />删除</a-button>
            </a-popconfirm>
          </a-space>
          <template v-else>—</template>
        </template>
      </template>
    </a-table>

    <!-- 创建/编辑弹窗：密文不回显，编辑留空表示不修改 -->
    <a-modal
      v-model:open="editVisible"
      :title="editing ? '编辑凭据' : '新建凭据'"
      :confirm-loading="editLoading"
      :width="560"
      @ok="onSubmitEdit"
    >
      <a-form layout="vertical">
        <div class="form-row">
          <a-form-item label="凭据名" required class="form-col">
            <a-input v-model:value="editForm.name" placeholder="如 生产root" />
          </a-form-item>
          <a-form-item v-if="needsLoginUser(editForm.auth_type)" label="登录用户" required class="form-col">
            <a-input v-model:value="editForm.login_user" placeholder="如 root" />
          </a-form-item>
        </div>
        <a-form-item label="凭据类型" required>
          <a-select v-model:value="editForm.auth_type" :options="editableAuthOptions" :disabled="Boolean(editing)" />
        </a-form-item>
        <a-form-item :label="editForm.auth_type === 'password' || editForm.auth_type === 'username_password' ? '密码' : editForm.auth_type === 'api_token' ? 'API Token' : editForm.auth_type === 'secret_file' ? '文本密钥文件内容' : 'SSH 私钥内容'" :required="!editing">
          <!-- 密码用掩码输入框，私钥用多行文本 -->
          <a-input-password
            v-if="editForm.auth_type === 'password' || editForm.auth_type === 'username_password' || editForm.auth_type === 'api_token'"
            v-model:value="editForm.secret"
            :placeholder="editing ? '留空表示不修改' : '请输入登录密码'"
          />
          <a-textarea
            v-else
            v-model:value="editForm.secret"
            :rows="6"
            :placeholder="editing ? '留空表示不修改' : editForm.auth_type === 'secret_file' ? '请输入 UTF-8 文本内容（最大 128 KiB）' : '-----BEGIN OPENSSH PRIVATE KEY-----'"
            class="secret-textarea"
          />
          <div v-if="editing" class="form-tip">
            出于安全考虑不回显已保存的密文；凭据类型创建后不可修改
          </div>
        </a-form-item>
        <a-form-item v-if="editForm.auth_type === 'secret_file'" label="文件名" required>
          <a-input v-model:value="editForm.file_name" placeholder="如 kubeconfig" />
        </a-form-item>
        <a-form-item v-if="editForm.auth_type === 'private_key'" label="私钥口令（可选）">
          <a-input-password
            v-model:value="editForm.passphrase"
            :placeholder="editing ? '仅在填写时更新口令' : '私钥有口令保护时填写'"
          />
        </a-form-item>
        <a-form-item label="说明">
          <a-textarea v-model:value="editForm.description" :rows="2" />
        </a-form-item>
      </a-form>
    </a-modal>
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
.auth-sel {
  width: 120px;
}
.toolbar-actions {
  margin-left: auto;
  display: flex;
  gap: 10px;
}
.form-row {
  display: flex;
  gap: 16px;
}
.form-col {
  flex: 1;
}
.form-tip {
  font-size: 12px;
  color: var(--text-3);
  margin-top: 6px;
}
.secret-textarea {
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
}
</style>
