<script setup lang="ts">
// 访问密钥：个人 API Token 管理（明文仅创建时展示一次，opsp_ 前缀 Bearer 直调平台 API）
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { CopyOutlined, KeyOutlined, PlusOutlined } from '@ant-design/icons-vue'
import * as tokenApi from '@/api/apiToken'

// ---------- 列表（每人上限 10 个，无需分页） ----------
const loading = ref(false)
const items = ref<tokenApi.ApiTokenItem[]>([])
const limit = ref(10)

const columns = [
  { title: '名称', dataIndex: 'name', key: 'name', width: 160, ellipsis: true },
  { title: '密钥', key: 'prefix', width: 160 },
  { title: '状态', key: 'status', width: 90 },
  { title: '过期时间', key: 'expires', width: 165 },
  { title: '最后使用', key: 'last_used', width: 165 },
  { title: '创建时间', key: 'created', width: 165 },
  { title: '操作', key: 'action', width: 80 },
]

async function loadList() {
  loading.value = true
  try {
    const data = await tokenApi.listTokens()
    items.value = data.items
    limit.value = data.limit
  } finally {
    loading.value = false
  }
}

// ---------- 创建 ----------
const createVisible = ref(false)
const createLoading = ref(false)
const createForm = reactive({ name: '', expires_in_days: 30 as number | null })
const expiryOptions = [
  { label: '30 天', value: 30 },
  { label: '90 天', value: 90 },
  { label: '365 天', value: 365 },
  { label: '永不过期', value: null },
]

function openCreate() {
  createForm.name = ''
  createForm.expires_in_days = 30
  createVisible.value = true
}

/** 创建成功后弹出明文展示窗（仅此一次机会复制） */
async function onSubmitCreate() {
  if (!createForm.name.trim()) {
    message.warning('请填写密钥名称')
    return
  }
  createLoading.value = true
  try {
    const data = await tokenApi.createToken(createForm.name.trim(), createForm.expires_in_days)
    createVisible.value = false
    plainToken.value = data.token
    resultVisible.value = true
    loadList()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    createLoading.value = false
  }
}

// ---------- 明文展示（一次性） ----------
const resultVisible = ref(false)
const plainToken = ref('')

async function onCopyToken() {
  try {
    await navigator.clipboard.writeText(plainToken.value)
    message.success('已复制到剪贴板')
  } catch {
    message.warning('复制失败，请手动选中复制')
  }
}

// ---------- 删除 ----------
async function onDelete(row: tokenApi.ApiTokenItem) {
  try {
    await tokenApi.deleteToken(row.id)
    message.success('密钥已删除，立即失效')
    loadList()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  }
}

function fmt(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : '—'
}

onMounted(loadList)
</script>

<template>
  <div>
    <!-- 彩色横幅：与其他页面同构 -->
    <div class="op-hero op-hero--teal">
      <div class="op-hero-icon"><KeyOutlined /></div>
      <div>
        <div class="op-hero-title">访问密钥</div>
        <div class="op-hero-sub">个人 API Token，可作为 Bearer 凭证直接调用平台接口</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat"><b>{{ items.length }}/{{ limit }}</b><span>已创建</span></div>
      </div>
    </div>

    <a-alert
      class="usage-tip"
      type="info"
      show-icon
      message="请求头携带 Authorization: Bearer <密钥> 即可调用平台 API，权限与本人实时一致；明文仅创建时显示一次，请妥善保管。"
    />

    <div class="toolbar">
      <div class="toolbar-actions">
        <a-button type="primary" :disabled="items.length >= limit" @click="openCreate">
          <PlusOutlined />新建密钥
        </a-button>
      </div>
    </div>

    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      row-key="id"
      bordered
      :pagination="false"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'prefix'">
          <code class="prefix">{{ record.prefix }}…</code>
        </template>
        <template v-else-if="column.key === 'status'">
          <a-tag v-if="record.expired" color="error">已过期</a-tag>
          <a-tag v-else color="success">有效</a-tag>
        </template>
        <template v-else-if="column.key === 'expires'">
          {{ record.expires_at ? fmt(record.expires_at) : '永不过期' }}
        </template>
        <template v-else-if="column.key === 'last_used'">{{ fmt(record.last_used_at) }}</template>
        <template v-else-if="column.key === 'created'">{{ fmt(record.created_at) }}</template>
        <template v-else-if="column.key === 'action'">
          <a-popconfirm title="删除后立即失效，确认删除？" @confirm="onDelete(record as tokenApi.ApiTokenItem)">
            <a-button size="small" danger>删除</a-button>
          </a-popconfirm>
        </template>
      </template>
    </a-table>

    <!-- 创建弹窗 -->
    <a-modal
      v-model:open="createVisible"
      title="新建访问密钥"
      :confirm-loading="createLoading"
      :width="440"
      @ok="onSubmitCreate"
    >
      <a-form layout="vertical">
        <a-form-item label="名称" required>
          <a-input
            v-model:value="createForm.name"
            :maxlength="64"
            placeholder="如 CI 发布脚本"
            @press-enter="onSubmitCreate"
          />
        </a-form-item>
        <a-form-item label="有效期">
          <a-select v-model:value="createForm.expires_in_days" :options="expiryOptions" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 明文展示弹窗（仅此一次） -->
    <a-modal v-model:open="resultVisible" title="密钥创建成功" :footer="null" :width="560">
      <a-alert
        type="warning"
        show-icon
        message="明文仅显示这一次，关闭后无法再查看，请立即复制保存。"
        class="once-tip"
      />
      <div class="token-box">
        <code>{{ plainToken }}</code>
        <a-button type="primary" size="small" @click="onCopyToken"><CopyOutlined />复制</a-button>
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.usage-tip {
  margin-bottom: 16px;
  border-radius: 10px;
}
.toolbar {
  display: flex;
  margin-bottom: 16px;
}
.toolbar-actions {
  margin-left: auto;
  display: flex;
  gap: 10px;
}
.prefix {
  background: var(--bg-hover);
  padding: 2px 6px;
  border-radius: 6px;
  font-size: 12px;
}
.once-tip {
  margin-bottom: 14px;
}
.token-box {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px dashed var(--border);
  border-radius: 10px;
  background: var(--bg-hover);
}
.token-box code {
  flex: 1;
  word-break: break-all;
  font-size: 13px;
}
</style>
