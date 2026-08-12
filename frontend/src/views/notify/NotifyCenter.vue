<script setup lang="ts">
// 通知中心（M6-3）：渠道配置（测试按钮）+ 事件映射 + 发送记录列表
// 权限 notify:config；敏感密钥读取时后端脱敏为 ******，原样提交不会覆盖真实值
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  ApiOutlined,
  BellOutlined,
  DownOutlined,
  MailOutlined,
  SendOutlined,
  WindowsOutlined,
} from '@ant-design/icons-vue'
import * as notifyApi from '@/api/notify'
import type { NotifyRecordItem } from '@/api/notify'
import ChannelTemplateFields from './ChannelTemplateFields.vue'

const loading = ref(false)
const activeTab = ref('channels')

// ---------- 展示元数据：事件/渠道/状态文案 ----------

/** 六类通知事件文案（PRD NOTIFY-02，含 M5 补充的执行中断） */
const eventText: Record<string, string> = {
  'ticket.pending_approval': '工单待审批',
  'ticket.approved': '审批通过',
  'ticket.rejected': '审批驳回',
  'execution.success': '执行成功',
  'execution.failed': '执行失败',
  'execution.interrupted': '执行中断',
}
const eventList = Object.keys(eventText)

/** 渠道文案（三个落地 + 三个预留） */
const channelText: Record<string, string> = {
  email: '邮件',
  webhook: 'Webhook',
  teams: 'Teams',
  dingtalk: '钉钉',
  feishu: '飞书',
  wecom: '企业微信',
}

/** 发送状态展示：pending 含重试排队中 */
const recordStatusMeta: Record<string, { text: string; color: string }> = {
  pending: { text: '待发送', color: 'gold' },
  success: { text: '成功', color: 'success' },
  failed: { text: '失败', color: 'error' },
}

// ---------- 渠道配置（Tab 1） ----------

/** 三个落地渠道的表单状态；secret 掩码 ****** 表示已配置且未修改 */
const channelForms = reactive<Record<string, { enabled: boolean; config: Record<string, any>; secret: string }>>({
  email: {
    enabled: false,
    config: {
      host: '', port: 465, username: '', from_addr: '', use_tls: true, starttls: false,
      templates: {},
    },
    secret: '',
  },
  webhook: { enabled: false, config: { url: '', templates: {} }, secret: '' },
  teams: { enabled: false, config: { url: '', templates: {} }, secret: '' },
})
const channelMetadata = reactive<Record<string, { events: Array<{ key: string; label: string }>; defaults: Record<string, Record<string, string>>; variables: Array<{ key: string; label: string; type: string; group: string; events: string[] }> }>>({})
const activeTemplateEvent = reactive<Record<string, string>>({ email: '', webhook: '', teams: '' })
const channelSaveVersion = reactive<Record<string, number>>({ email: 0, webhook: 0, teams: 0 })
const collapsedChannels = reactive<Record<string, boolean>>({ email: false, webhook: false, teams: false, more: false })
function toggleChannel(type: string) {
  collapsedChannels[type] = !collapsedChannels[type]
}
/** 预留渠道列表（不可启用，仅展示占位） */
const reservedChannels = ref<string[]>([])

/** 拉取渠道配置并回填表单（config 合并保留表单默认键） */
async function loadChannels() {
  const data = await notifyApi.listChannels()
  reservedChannels.value = data.items.filter((c) => !c.implemented).map((c) => c.type)
  for (const item of data.items) {
    const form = channelForms[item.type]
    if (!form) continue
    form.enabled = item.enabled
    Object.assign(form.config, item.config || {})
    channelMetadata[item.type] = {
      events: item.template_events,
      defaults: item.template_defaults,
      variables: item.template_variables,
    }
    activeTemplateEvent[item.type] = item.template_events?.[0]?.key || ''
    form.secret = item.secret ?? ''
  }
}

/** 保存单个渠道：secret 原样提交（****** 不覆盖真实密钥，空串 = 清空） */
const savingChannel = ref('')
async function onSaveChannel(type: string) {
  const form = channelForms[type]
  if (form.enabled) {
    // 启用时做最小必填校验，避免保存出"启用但必配置为空"的无效渠道
    if (type === 'email' && !form.config.host) {
      message.warning('请填写 SMTP 服务器地址')
      return
    }
    if ((type === 'webhook' || type === 'teams') && !form.config.url) {
      message.warning('请填写 Webhook URL')
      return
    }
  }
  savingChannel.value = type
  try {
    await notifyApi.updateChannel(type, {
      enabled: form.enabled,
      config: { ...form.config },
      secret: form.secret,
    })
    message.success('已保存，即时生效')
    channelSaveVersion[type] = (channelSaveVersion[type] || 0) + 1
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    savingChannel.value = ''
  }
}

// Email 测试收件邮箱（仅测试用，不落库）
const testReceiver = ref('')
const testingChannel = ref('')
const testResults = reactive<Record<string, { success: boolean; message: string } | null>>({
  email: null,
  webhook: null,
  teams: null,
})

/** 测试发送：传当前表单值（非已保存配置），保存前可预测 */
async function onTestChannel(type: string) {
  const form = channelForms[type]
  if (type === 'email' && !testReceiver.value) {
    message.warning('请填写测试收件邮箱')
    return
  }
  testingChannel.value = type
  testResults[type] = null
  try {
    testResults[type] = await notifyApi.testChannel(type, {
      config: { ...form.config },
      secret: form.secret,
      event: activeTemplateEvent[type] || channelMetadata[type]?.events?.[0]?.key,
      receiver: type === 'email' ? testReceiver.value : undefined,
    })
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    testingChannel.value = ''
  }
}

// ---------- 事件映射（Tab 2） ----------

/** 映射编辑状态：{事件: 勾选的渠道列表}，全量提交 */
const mappingState = reactive<Record<string, string[]>>({})
const mappingOptions = [
  { label: '邮件', value: 'email' },
  { label: 'Webhook', value: 'webhook' },
  { label: 'Teams', value: 'teams' },
]

async function loadMappings() {
  const data = await notifyApi.listEventMappings()
  for (const item of data.items) {
    // 只保留落地渠道的勾选（预留渠道映射不在 UI 编辑范围）
    mappingState[item.event] = item.channels.filter((c) => mappingOptions.some((o) => o.value === c))
  }
}

const savingMapping = ref(false)
/** 全量提交映射：未勾选的事件即清空该事件的所有渠道 */
async function onSaveMappings() {
  savingMapping.value = true
  try {
    await notifyApi.updateEventMappings({ ...mappingState })
    message.success('映射已保存，即时生效')
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    savingMapping.value = false
  }
}

// ---------- 发送记录（Tab 3） ----------

const records = ref<NotifyRecordItem[]>([])
const recordTotal = ref(0)
const recordLoading = ref(false)
const recordQuery = reactive({
  page: 1,
  page_size: 20,
  event: undefined as string | undefined,
  channel: undefined as string | undefined,
  status: undefined as string | undefined,
  range: [] as string[],
})

/** 拉取发送记录（筛选 + 分页） */
async function loadRecords() {
  recordLoading.value = true
  try {
    const data = await notifyApi.listRecords({
      page: recordQuery.page,
      page_size: recordQuery.page_size,
      event: recordQuery.event,
      channel: recordQuery.channel,
      status: recordQuery.status,
      start: recordQuery.range?.[0] || undefined,
      end: recordQuery.range?.[1] || undefined,
    })
    records.value = data.items
    recordTotal.value = data.total
  } finally {
    recordLoading.value = false
  }
}

/** 筛选变化：回第一页重查 */
function onRecordFilterChange() {
  recordQuery.page = 1
  loadRecords()
}

function onRecordPageChange(page: number, pageSize: number) {
  recordQuery.page = page
  recordQuery.page_size = pageSize
  loadRecords()
}

const recordColumns = [
  { title: '时间', dataIndex: 'created_at', width: 160 },
  { title: '事件', dataIndex: 'event', width: 120 },
  { title: '渠道', dataIndex: 'channel_type', width: 100 },
  { title: '标题', dataIndex: 'title', ellipsis: true },
  { title: '收件人', dataIndex: 'receiver', width: 140, ellipsis: true },
  { title: '状态', dataIndex: 'status', width: 150 },
  { title: '发送时间', dataIndex: 'sent_at', width: 160 },
]

// ---------- 横幅统计 ----------

const enabledCount = computed(() => Object.values(channelForms).filter((f) => f.enabled).length)
const mappingCount = computed(() => Object.values(mappingState).reduce((n, c) => n + c.length, 0))

onMounted(async () => {
  loading.value = true
  try {
    await Promise.all([loadChannels(), loadMappings(), loadRecords()])
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <a-spin :spinning="loading">
    <div>
      <!-- 彩色横幅：与 M1 系统设置同款式（op-hero），随渠道/映射状态实时联动 -->
      <div class="op-hero op-hero--amber">
        <div class="op-hero-icon"><BellOutlined /></div>
        <div>
          <div class="op-hero-title">通知中心</div>
          <div class="op-hero-sub">渠道配置、事件映射与发送记录；失败自动退避重试（1/5/15 分钟，最多 3 次）</div>
        </div>
        <div class="op-hero-extra">
          <div class="op-hero-stat"><b>{{ enabledCount }}/3</b><span>启用渠道</span></div>
          <div class="op-hero-stat"><b>{{ mappingCount }}</b><span>事件映射</span></div>
          <div class="op-hero-stat"><b>{{ recordTotal }}</b><span>发送记录</span></div>
        </div>
      </div>

      <a-tabs v-model:activeKey="activeTab">
        <!-- ===== Tab 1 渠道配置 ===== -->
        <a-tab-pane key="channels" tab="渠道配置">
          <div class="blocks">
            <!-- Email -->
            <a-card class="block collapsible-block">
              <div class="block-head" :class="{ 'is-collapsed': collapsedChannels.email }" @click="toggleChannel('email')">
                <div class="op-icon-grad" style="background: var(--grad-blue)"><MailOutlined /></div>
                <div class="block-title">
                  <b>邮件 Email</b>
                  <span>SMTP 发送；收件人取工单相关用户的邮箱（未配置邮箱的用户跳过）</span>
                </div>
                <a-switch v-model:checked="channelForms.email.enabled" class="head-switch" @click.stop />
                <a-button
                  :loading="testingChannel === 'email'"
                  @click.stop="onTestChannel('email')"
                >测试发送</a-button>
                <a-button
                  type="primary"
                  :loading="savingChannel === 'email'"
                  @click.stop="onSaveChannel('email')"
                >保存</a-button>
                <DownOutlined class="collapse-icon" />
              </div>
              <div v-show="!collapsedChannels.email" class="block-body">
              <template v-if="channelForms.email.enabled">
                <div class="grid2">
                  <a-form-item label="SMTP 服务器" required>
                    <a-input v-model:value="channelForms.email.config.host" placeholder="smtp.corp.com" />
                  </a-form-item>
                  <a-form-item label="端口">
                    <a-input-number
                      v-model:value="channelForms.email.config.port"
                      :min="1" :max="65535" style="width: 100%"
                    />
                  </a-form-item>
                  <a-form-item label="账号">
                    <a-input v-model:value="channelForms.email.config.username" placeholder="noreply@corp.com" />
                  </a-form-item>
                  <a-form-item label="密码 / 授权码">
                    <a-input-password
                      v-model:value="channelForms.email.secret"
                      placeholder="******（不修改则保持原值）"
                    />
                  </a-form-item>
                  <a-form-item label="发件人地址">
                    <a-input v-model:value="channelForms.email.config.from_addr" placeholder="留空使用账号" />
                  </a-form-item>
                  <a-form-item label="加密方式">
                    <a-space :size="16">
                      <span class="inline-item">
                        SSL/TLS（465）
                        <a-switch v-model:checked="channelForms.email.config.use_tls" />
                      </span>
                      <span class="inline-item">
                        STARTTLS（587）
                        <a-switch
                          v-model:checked="channelForms.email.config.starttls"
                          :disabled="channelForms.email.config.use_tls"
                        />
                      </span>
                    </a-space>
                  </a-form-item>
                  <a-form-item label="测试收件邮箱">
                    <a-input v-model:value="testReceiver" placeholder="you@corp.com（仅测试用，不保存）" />
                  </a-form-item>
                </div>
                <ChannelTemplateFields
                  :config="channelForms.email.config"
                  channel-type="email"
                  :events="channelMetadata.email?.events || []"
                  :defaults="channelMetadata.email?.defaults || {}"
                  :variables="channelMetadata.email?.variables || []"
                  :save-version="channelSaveVersion.email"
                  @event-change="activeTemplateEvent.email = $event"
                />
                <a-alert
                  v-if="testResults.email"
                  :type="testResults.email.success ? 'success' : 'error'"
                  :message="testResults.email.success ? '测试邮件已发出，请查收' : `发送失败：${testResults.email.message}`"
                  show-icon
                />
              </template>
              <div v-else class="disabled-tip">已停用：邮件事件将记为发送失败（渠道未启用）</div>
              </div>
            </a-card>

            <!-- Webhook -->
            <a-card class="block collapsible-block">
              <div class="block-head" :class="{ 'is-collapsed': collapsedChannels.webhook }" @click="toggleChannel('webhook')">
                <div class="op-icon-grad" style="background: var(--grad-purple)"><ApiOutlined /></div>
                <div class="block-title">
                  <b>Webhook</b>
                  <span>JSON POST 到全局地址；配置签名密钥后附 HMAC-SHA256 签名头（X-Ops-Signature）</span>
                </div>
                <a-switch v-model:checked="channelForms.webhook.enabled" class="head-switch" @click.stop />
                <a-button
                  :loading="testingChannel === 'webhook'"
                  @click.stop="onTestChannel('webhook')"
                >测试发送</a-button>
                <a-button
                  type="primary"
                  :loading="savingChannel === 'webhook'"
                  @click.stop="onSaveChannel('webhook')"
                >保存</a-button>
                <DownOutlined class="collapse-icon" />
              </div>
              <div v-show="!collapsedChannels.webhook" class="block-body">
              <template v-if="channelForms.webhook.enabled">
                <div class="grid2">
                  <a-form-item label="Webhook URL" required>
                    <a-input v-model:value="channelForms.webhook.config.url" placeholder="https://hooks.corp.com/opspilot" />
                  </a-form-item>
                  <a-form-item label="签名密钥">
                    <a-input-password
                      v-model:value="channelForms.webhook.secret"
                      placeholder="******（可空 = 不签名；不修改则保持原值）"
                    />
                  </a-form-item>
                </div>
                <ChannelTemplateFields
                  :config="channelForms.webhook.config"
                  channel-type="webhook"
                  :events="channelMetadata.webhook?.events || []"
                  :defaults="channelMetadata.webhook?.defaults || {}"
                  :variables="channelMetadata.webhook?.variables || []"
                  :save-version="channelSaveVersion.webhook"
                  @event-change="activeTemplateEvent.webhook = $event"
                />
                <a-alert
                  v-if="testResults.webhook"
                  :type="testResults.webhook.success ? 'success' : 'error'"
                  :message="testResults.webhook.success ? '测试消息已推送' : `推送失败：${testResults.webhook.message}`"
                  show-icon
                />
              </template>
              <div v-else class="disabled-tip">已停用：Webhook 事件将记为发送失败（渠道未启用）</div>
              </div>
            </a-card>

            <!-- Teams -->
            <a-card class="block collapsible-block">
              <div class="block-head" :class="{ 'is-collapsed': collapsedChannels.teams }" @click="toggleChannel('teams')">
                <div class="op-icon-grad" style="background: var(--grad-green)"><WindowsOutlined /></div>
                <div class="block-title">
                  <b>Microsoft Teams</b>
                  <span>MessageCard 卡片推送到频道 Incoming Webhook</span>
                </div>
                <a-switch v-model:checked="channelForms.teams.enabled" class="head-switch" @click.stop />
                <a-button
                  :loading="testingChannel === 'teams'"
                  @click.stop="onTestChannel('teams')"
                >测试发送</a-button>
                <a-button
                  type="primary"
                  :loading="savingChannel === 'teams'"
                  @click.stop="onSaveChannel('teams')"
                >保存</a-button>
                <DownOutlined class="collapse-icon" />
              </div>
              <div v-show="!collapsedChannels.teams" class="block-body">
              <template v-if="channelForms.teams.enabled">
                <div class="grid2">
                  <a-form-item label="Incoming Webhook URL" required>
                    <a-input
                      v-model:value="channelForms.teams.config.url"
                      placeholder="https://xxx.webhook.office.com/webhookb2/..."
                    />
                  </a-form-item>
                </div>
                <ChannelTemplateFields
                  :config="channelForms.teams.config"
                  channel-type="teams"
                  :events="channelMetadata.teams?.events || []"
                  :defaults="channelMetadata.teams?.defaults || {}"
                  :variables="channelMetadata.teams?.variables || []"
                  :save-version="channelSaveVersion.teams"
                  @event-change="activeTemplateEvent.teams = $event"
                />
                <a-alert
                  v-if="testResults.teams"
                  :type="testResults.teams.success ? 'success' : 'error'"
                  :message="testResults.teams.success ? '测试卡片已推送' : `推送失败：${testResults.teams.message}`"
                  show-icon
                />
              </template>
              <div v-else class="disabled-tip">已停用：Teams 事件将记为发送失败（渠道未启用）</div>
              </div>
            </a-card>

            <!-- 预留渠道占位 -->
            <a-card class="block collapsible-block">
              <div class="block-head" :class="{ 'is-collapsed': collapsedChannels.more }" @click="toggleChannel('more')">
                <div class="op-icon-grad" style="background: var(--grad-orange)"><SendOutlined /></div>
                <div class="block-title">
                  <b>更多渠道</b>
                  <span>接口已预留，后续版本开放配置</span>
                </div>
                <DownOutlined class="collapse-icon" />
              </div>
              <div v-show="!collapsedChannels.more" class="block-body">
              <a-space :size="8" wrap>
                <a-tag v-for="c in reservedChannels" :key="c">{{ channelText[c] || c }}（预留）</a-tag>
              </a-space>
              </div>
            </a-card>
          </div>
        </a-tab-pane>

        <!-- ===== Tab 2 事件映射 ===== -->
        <a-tab-pane key="events" tab="事件映射">
          <a-card class="block">
            <div class="block-head">
              <div class="op-icon-grad" style="background: var(--grad-purple)"><BellOutlined /></div>
              <div class="block-title">
                <b>事件-渠道映射</b>
                <span>勾选每类事件经哪些渠道发送；保存为全量覆盖，未勾选即不发送</span>
              </div>
              <a-button type="primary" :loading="savingMapping" @click="onSaveMappings">保存映射</a-button>
            </div>
            <div class="map-table">
              <div class="map-row map-row--head">
                <div class="map-event">事件</div>
                <div class="map-channels">发送渠道</div>
              </div>
              <div v-for="ev in eventList" :key="ev" class="map-row">
                <div class="map-event">
                  <b>{{ eventText[ev] }}</b>
                  <span>{{ ev }}</span>
                </div>
                <div class="map-channels">
                  <a-checkbox-group v-model:value="mappingState[ev]" :options="mappingOptions" />
                </div>
              </div>
            </div>
            <div class="field-tip">
              勾选后仍需在「渠道配置」中启用对应渠道才会真实发送；未启用渠道的通知将记为失败留痕。
            </div>
          </a-card>
        </a-tab-pane>

        <!-- ===== Tab 3 发送记录 ===== -->
        <a-tab-pane key="records" tab="发送记录">
          <a-card class="block">
            <!-- 筛选条 -->
            <div class="filter-bar">
              <a-select
                v-model:value="recordQuery.event"
                placeholder="事件"
                style="width: 150px"
                allow-clear
                @change="onRecordFilterChange"
              >
                <a-select-option v-for="ev in eventList" :key="ev" :value="ev">
                  {{ eventText[ev] }}
                </a-select-option>
              </a-select>
              <a-select
                v-model:value="recordQuery.channel"
                placeholder="渠道"
                style="width: 130px"
                allow-clear
                @change="onRecordFilterChange"
              >
                <a-select-option v-for="(label, value) in channelText" :key="value" :value="value">
                  {{ label }}
                </a-select-option>
              </a-select>
              <a-select
                v-model:value="recordQuery.status"
                placeholder="状态"
                style="width: 120px"
                allow-clear
                @change="onRecordFilterChange"
              >
                <a-select-option v-for="(meta, value) in recordStatusMeta" :key="value" :value="value">
                  {{ meta.text }}
                </a-select-option>
              </a-select>
              <a-range-picker
                v-model:value="recordQuery.range"
                value-format="YYYY-MM-DD"
                @change="onRecordFilterChange"
              />
            </div>

            <a-table
              :columns="recordColumns"
              :data-source="records"
              :loading="recordLoading"
              row-key="id"
              size="middle"
              :pagination="{
                current: recordQuery.page,
                pageSize: recordQuery.page_size,
                total: recordTotal,
                showSizeChanger: true,
                showQuickJumper: true,
                showTotal: (t: number) => `共 ${t} 条`,
                onChange: onRecordPageChange,
              }"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.dataIndex === 'event'">
                  <a-tag>{{ eventText[record.event] || record.event }}</a-tag>
                </template>
                <template v-else-if="column.dataIndex === 'channel_type'">
                  {{ channelText[record.channel_type] || record.channel_type }}
                </template>
                <template v-else-if="column.dataIndex === 'receiver'">
                  <span :title="record.receiver || ''">{{ record.receiver || '—' }}</span>
                </template>
                <template v-else-if="column.dataIndex === 'status'">
                  <!-- 失败原因悬浮展示；pending 带重试进度（第 N 次 / 下次重试时间） -->
                  <a-tooltip :title="record.error || undefined">
                    <a-tag :color="recordStatusMeta[record.status]?.color">
                      {{ recordStatusMeta[record.status]?.text || record.status }}
                    </a-tag>
                  </a-tooltip>
                  <span v-if="record.retry_count > 0" class="retry-hint">
                    重试 {{ record.retry_count }}/3
                    <template v-if="record.status === 'pending' && record.next_retry_at">
                      · 下次 {{ record.next_retry_at }}
                    </template>
                  </span>
                </template>
                <template v-else-if="column.dataIndex === 'sent_at'">
                  {{ record.sent_at || '—' }}
                </template>
              </template>
            </a-table>
          </a-card>
        </a-tab-pane>
      </a-tabs>
    </div>
  </a-spin>
</template>

<style scoped>
.blocks {
  display: grid;
  grid-template-columns: 1fr;
  gap: 16px;
  width: 100%;
}
.block {
  border-radius: 14px;
  min-width: 0;
}
.block-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}
.collapsible-block .block-head {
  cursor: pointer;
  user-select: none;
}
.collapsible-block .block-head.is-collapsed {
  margin-bottom: 0;
}
.collapse-icon {
  color: var(--text-3);
  transition: transform .2s ease;
}
.block-head.is-collapsed .collapse-icon {
  transform: rotate(-90deg);
}
.block-body {
  min-width: 0;
}
.block-title {
  flex: 1;
  min-width: 0;
}
.block-title b {
  font-size: 15px;
  color: var(--text-1);
  display: block;
}
.block-title span {
  font-size: 12px;
  color: var(--text-3);
  display: block;
  margin-top: 4px;
}
.head-switch {
  margin-right: 4px;
}
.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: 16px;
}
.grid2 :deep(.ant-form-item) {
  margin-bottom: 14px;
}
.inline-item {
  font-size: 13px;
  color: var(--text-2);
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.field-tip {
  font-size: 12px;
  color: var(--text-3);
  margin-top: 12px;
}
.disabled-tip {
  font-size: 13px;
  color: var(--text-3);
  padding: 6px 0;
}

/* 事件映射矩阵 */
.map-table {
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}
.map-row {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  border-top: 1px solid var(--border);
}
.map-row:first-child {
  border-top: none;
}
.map-row--head {
  background: var(--bg-hover);
  font-size: 12px;
  font-weight: 600;
  color: var(--text-3);
  padding: 8px 16px;
}
.map-event {
  width: 220px;
  flex: none;
}
.map-event b {
  font-size: 13px;
  color: var(--text-1);
  display: block;
}
.map-event span {
  font-size: 12px;
  color: var(--text-3);
  display: block;
  margin-top: 2px;
}
.map-channels {
  flex: 1;
}

/* 发送记录 */
.filter-bar {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.retry-hint {
  font-size: 12px;
  color: var(--text-3);
  margin-left: 4px;
}
</style>
