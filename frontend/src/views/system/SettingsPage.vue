<script setup lang="ts">
// 系统设置：安全策略（MFA/令牌/密码）+ SSO 认证源（LDAP / OIDC）+ Ansible 作业主机（M5）
// 权限 system:config；敏感密钥读取时后端脱敏为 ******，原样提交不会覆盖真实值
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  ApiOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
  KeyOutlined,
  LockOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
} from '@ant-design/icons-vue'
import { listCredentials } from '@/api/job'
import * as sysApi from '@/api/system'

const loading = ref(false)

// ---------- 各配置块的编辑状态 ----------
const mfaPolicy = ref<'off' | 'optional' | 'required'>('off')
const tokenPolicy = reactive({
  access_minutes: null as number | null,
  refresh_days: null as number | null,
})
const pwdPolicy = reactive({
  min_length: 8,
  require_complex: true,
  max_fail: 5,
  lock_minutes: 15,
})

// LDAP：enabled 开关映射 ldap.config 是否为 null
const ldapEnabled = ref(false)
const ldapForm = reactive({
  server_url: '',
  bind_dn: '',
  bind_password: '',
  base_dn: '',
  user_filter: '(uid={username})',
  attr_display_name: 'cn',
  attr_email: 'mail',
})
const ldapDefaultRole = ref('ops')

// OIDC（OAuth2 授权码模式）
const oidcEnabled = ref(false)
const oidcForm = reactive({
  authorize_endpoint: '',
  token_endpoint: '',
  userinfo_endpoint: '',
  client_id: '',
  client_secret: '',
  redirect_uri: '',
  scope: 'openid profile email',
})
const oidcDefaultRole = ref('ops')

// Ansible 作业主机（M5）：enabled 开关映射 ansible.job_host 是否为 null
const jobHostEnabled = ref(false)
const jobHostForm = reactive<sysApi.JobHostConfig>({
  ip: '',
  port: 22,
  credential_id: null,
  workdir: '/tmp',
})
const credOptions = ref<{ label: string; value: number }[]>([])

const roleOptions = ref<{ label: string; value: string }[]>([])

// 横幅状态统计：与表单实时联动
const mfaPolicyLabel = computed(
  () => ({ off: '关闭', optional: '自愿', required: '强制' })[mfaPolicy.value],
)

/** 拉取全部配置并回填表单 */
async function loadConfigs() {
  loading.value = true
  try {
    const [cfg, roleData] = await Promise.all([
      sysApi.getConfigs(),
      sysApi.listRoles(),
    ])
    roleOptions.value = roleData.items.map((r) => ({ label: `${r.name}（${r.code}）`, value: r.code }))

    mfaPolicy.value = (cfg['mfa.policy'] as typeof mfaPolicy.value) || 'off'

    const token = (cfg['token.policy'] as Record<string, number | null>) || {}
    tokenPolicy.access_minutes = token.access_minutes ?? null
    tokenPolicy.refresh_days = token.refresh_days ?? null

    Object.assign(pwdPolicy, (cfg['password.policy'] as object) || {})

    const ldap = cfg['ldap.config'] as Record<string, string> | null
    ldapEnabled.value = !!ldap?.server_url
    if (ldap) Object.assign(ldapForm, ldap)
    ldapDefaultRole.value = (cfg['ldap.default_role'] as string) || 'ops'

    const oidc = cfg['oidc.config'] as Record<string, string> | null
    oidcEnabled.value = !!oidc?.authorize_endpoint
    if (oidc) Object.assign(oidcForm, oidc)
    oidcDefaultRole.value = (cfg['oidc.default_role'] as string) || 'ops'

    const jobHost = cfg['ansible.job_host'] as sysApi.JobHostConfig | null
    jobHostEnabled.value = !!jobHost?.ip
    if (jobHost) Object.assign(jobHostForm, jobHost)
  } finally {
    loading.value = false
  }
  // 凭据下拉单独拉取：失败不阻断其他配置块加载（需 credential:read）
  try {
    const creds = await listCredentials({ page: 1, page_size: 100 })
    credOptions.value = creds.items.map((c) => ({ label: `${c.name}（${c.login_user}）`, value: c.id }))
  } catch {
    /* 无凭据权限时下拉为空，提示由拦截器弹出 */
  }
}

/** 通用保存：写入配置键后提示 */
const saving = ref('')
async function save(name: string, configs: Record<string, unknown>) {
  saving.value = name
  try {
    await sysApi.updateConfigs(configs)
    message.success('已保存，即时生效')
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    saving.value = ''
  }
}

const onSaveSecurity = () =>
  save('security', {
    'mfa.policy': mfaPolicy.value,
    'token.policy': { ...tokenPolicy },
    'password.policy': { ...pwdPolicy },
  })

function onSaveLdap() {
  if (ldapEnabled.value && (!ldapForm.server_url || !ldapForm.base_dn)) {
    message.warning('请填写 LDAP 服务器地址与 Base DN')
    return
  }
  save('ldap', {
    // 关闭开关即置空配置，后端认证链将跳过 LDAP 源
    'ldap.config': ldapEnabled.value ? { ...ldapForm } : null,
    'ldap.default_role': ldapDefaultRole.value,
  })
}

function onSaveOidc() {
  if (
    oidcEnabled.value &&
    (!oidcForm.authorize_endpoint || !oidcForm.token_endpoint || !oidcForm.client_id)
  ) {
    message.warning('请填写授权端点、令牌端点与 Client ID')
    return
  }
  save('oidc', {
    'oidc.config': oidcEnabled.value ? { ...oidcForm } : null,
    'oidc.default_role': oidcDefaultRole.value,
  })
}

// ---------- Ansible 作业主机（M5） ----------

function onSaveJobHost() {
  if (jobHostEnabled.value && (!jobHostForm.ip || !jobHostForm.credential_id)) {
    message.warning('请填写作业主机 IP 并选择 SSH 凭据')
    return
  }
  save('jobhost', {
    // 关闭开关即置空配置，playbook 步骤执行时将报作业主机未配置
    'ansible.job_host': jobHostEnabled.value ? { ...jobHostForm } : null,
  })
}

/** 连通性测试：传当前表单值（非已保存配置），保存前可预测 */
const testing = ref(false)
const testResult = ref<{ success: boolean; message: string } | null>(null)

async function onTestJobHost() {
  if (!jobHostForm.ip || !jobHostForm.credential_id) {
    message.warning('请先填写作业主机 IP 并选择 SSH 凭据')
    return
  }
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await sysApi.testJobHost({
      ip: jobHostForm.ip,
      port: jobHostForm.port,
      credential_id: jobHostForm.credential_id,
    })
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    testing.value = false
  }
}

onMounted(loadConfigs)
</script>

<template>
  <a-spin :spinning="loading">
    <div>
      <!-- 彩色横幅：铺满内容区（不受下方配置卡片限宽约束），带实时状态统计 -->
      <div class="op-hero op-hero--teal">
        <div class="op-hero-icon"><SettingOutlined /></div>
        <div>
          <div class="op-hero-title">系统设置</div>
          <div class="op-hero-sub">安全策略、SSO 认证源与 Ansible 作业主机配置，保存后即时生效</div>
        </div>
        <div class="op-hero-extra">
          <div class="op-hero-stat"><b>{{ mfaPolicyLabel }}</b><span>MFA 策略</span></div>
          <div class="op-hero-stat"><b>{{ ldapEnabled ? '启用' : '停用' }}</b><span>LDAP</span></div>
          <div class="op-hero-stat"><b>{{ oidcEnabled ? '启用' : '停用' }}</b><span>OIDC</span></div>
          <div class="op-hero-stat"><b>{{ jobHostEnabled ? '已配置' : '未配置' }}</b><span>作业主机</span></div>
        </div>
      </div>

      <div class="settings">
      <!-- 安全策略 -->
      <a-card class="block">
        <div class="block-head">
          <div class="op-icon-grad" style="background: var(--grad-blue)"><SafetyCertificateOutlined /></div>
          <div class="block-title">
            <b>安全策略</b>
            <span>MFA 多因子认证、登录令牌有效期与密码策略</span>
          </div>
          <a-button type="primary" :loading="saving === 'security'" @click="onSaveSecurity">
            保存
          </a-button>
        </div>

        <div class="field">
          <div class="field-label"><LockOutlined /> MFA 策略</div>
          <a-radio-group v-model:value="mfaPolicy">
            <a-radio-button value="off">关闭</a-radio-button>
            <a-radio-button value="optional">自愿开启</a-radio-button>
            <a-radio-button value="required">强制所有用户开启</a-radio-button>
          </a-radio-group>
          <div class="field-tip">
            强制模式下未绑定用户登录时将被引导绑定认证器；用户已自行绑定 MFA 的，无论策略如何登录都需验证动态码。
          </div>
        </div>

        <div class="field">
          <div class="field-label"><ClockCircleOutlined /> 登录令牌有效期</div>
          <a-space :size="16" wrap>
            <span class="inline-item">
              Access Token
              <a-input-number
                v-model:value="tokenPolicy.access_minutes"
                :min="5"
                :max="1440"
                placeholder="默认 15"
              />
              分钟
            </span>
            <span class="inline-item">
              Refresh Token
              <a-input-number
                v-model:value="tokenPolicy.refresh_days"
                :min="1"
                :max="90"
                placeholder="默认 7"
              />
              天
            </span>
          </a-space>
          <div class="field-tip">留空使用部署默认值；修改后对新签发的令牌生效，已登录会话不受影响。</div>
        </div>

        <div class="field">
          <div class="field-label"><KeyOutlined /> 密码策略</div>
          <a-space :size="16" wrap>
            <span class="inline-item">
              最小长度
              <a-input-number v-model:value="pwdPolicy.min_length" :min="6" :max="32" />
            </span>
            <span class="inline-item">
              要求字母+数字
              <a-switch v-model:checked="pwdPolicy.require_complex" />
            </span>
            <span class="inline-item">
              失败锁定阈值
              <a-input-number v-model:value="pwdPolicy.max_fail" :min="3" :max="10" />
              次
            </span>
            <span class="inline-item">
              锁定时长
              <a-input-number v-model:value="pwdPolicy.lock_minutes" :min="5" :max="120" />
              分钟
            </span>
          </a-space>
        </div>
      </a-card>

      <!-- LDAP 认证源 -->
      <a-card class="block">
        <div class="block-head">
          <div class="op-icon-grad" style="background: var(--grad-purple)"><ApiOutlined /></div>
          <div class="block-title">
            <b>LDAP 认证</b>
            <span>企业目录账号密码登录，首次登录自动创建用户</span>
          </div>
          <a-switch v-model:checked="ldapEnabled" class="head-switch" />
          <a-button type="primary" :loading="saving === 'ldap'" @click="onSaveLdap">保存</a-button>
        </div>

        <template v-if="ldapEnabled">
          <div class="grid2">
            <a-form-item label="服务器地址" required>
              <a-input v-model:value="ldapForm.server_url" placeholder="ldap://ldap.corp.com:389" />
            </a-form-item>
            <a-form-item label="Base DN" required>
              <a-input v-model:value="ldapForm.base_dn" placeholder="ou=people,dc=corp,dc=com" />
            </a-form-item>
            <a-form-item label="服务账号 Bind DN">
              <a-input v-model:value="ldapForm.bind_dn" placeholder="cn=svc,dc=corp,dc=com" />
            </a-form-item>
            <a-form-item label="服务账号密码">
              <a-input-password
                v-model:value="ldapForm.bind_password"
                placeholder="******（不修改则保持原值）"
              />
            </a-form-item>
            <a-form-item label="用户过滤器">
              <a-input v-model:value="ldapForm.user_filter" placeholder="(uid={username})" />
            </a-form-item>
            <a-form-item label="首登默认角色">
              <a-select v-model:value="ldapDefaultRole" :options="roleOptions" />
            </a-form-item>
            <a-form-item label="显示名属性">
              <a-input v-model:value="ldapForm.attr_display_name" placeholder="cn" />
            </a-form-item>
            <a-form-item label="邮箱属性">
              <a-input v-model:value="ldapForm.attr_email" placeholder="mail" />
            </a-form-item>
          </div>
        </template>
        <div v-else class="disabled-tip">已停用：登录时不再尝试 LDAP 认证源</div>
      </a-card>

      <!-- OIDC 认证源 -->
      <a-card class="block">
        <div class="block-head">
          <div class="op-icon-grad" style="background: var(--grad-orange)"><KeyOutlined /></div>
          <div class="block-title">
            <b>OIDC / OAuth2 认证</b>
            <span>授权码模式单点登录，登录页显示 SSO 入口</span>
          </div>
          <a-switch v-model:checked="oidcEnabled" class="head-switch" />
          <a-button type="primary" :loading="saving === 'oidc'" @click="onSaveOidc">保存</a-button>
        </div>

        <template v-if="oidcEnabled">
          <div class="grid2">
            <a-form-item label="授权端点 Authorize Endpoint" required>
              <a-input
                v-model:value="oidcForm.authorize_endpoint"
                placeholder="https://idp.corp.com/oauth2/authorize"
              />
            </a-form-item>
            <a-form-item label="令牌端点 Token Endpoint" required>
              <a-input
                v-model:value="oidcForm.token_endpoint"
                placeholder="https://idp.corp.com/oauth2/token"
              />
            </a-form-item>
            <a-form-item label="用户信息端点 UserInfo Endpoint">
              <a-input
                v-model:value="oidcForm.userinfo_endpoint"
                placeholder="https://idp.corp.com/oauth2/userinfo"
              />
            </a-form-item>
            <a-form-item label="回调地址 Redirect URI">
              <a-input
                v-model:value="oidcForm.redirect_uri"
                placeholder="http://本平台地址/api/v1/auth/sso/oidc/callback"
              />
            </a-form-item>
            <a-form-item label="Client ID" required>
              <a-input v-model:value="oidcForm.client_id" />
            </a-form-item>
            <a-form-item label="Client Secret">
              <a-input-password
                v-model:value="oidcForm.client_secret"
                placeholder="******（不修改则保持原值）"
              />
            </a-form-item>
            <a-form-item label="Scope">
              <a-input v-model:value="oidcForm.scope" placeholder="openid profile email" />
            </a-form-item>
            <a-form-item label="首登默认角色">
              <a-select v-model:value="oidcDefaultRole" :options="roleOptions" />
            </a-form-item>
          </div>
        </template>
        <div v-else class="disabled-tip">已停用：登录页不显示 SSO 入口</div>
      </a-card>

      <!-- Ansible 作业主机（M5：playbook 步骤的控制节点，全局单实例） -->
      <a-card class="block">
        <div class="block-head">
          <div class="op-icon-grad" style="background: var(--grad-green)"><CloudServerOutlined /></div>
          <div class="block-title">
            <b>Ansible 作业主机</b>
            <span>Playbook 步骤的控制节点：平台 SSH 到该主机运行 ansible-playbook</span>
          </div>
          <a-switch v-model:checked="jobHostEnabled" class="head-switch" />
          <a-button :loading="testing" :disabled="!jobHostEnabled" @click="onTestJobHost">测试连通性</a-button>
          <a-button type="primary" :loading="saving === 'jobhost'" @click="onSaveJobHost">保存</a-button>
        </div>

        <template v-if="jobHostEnabled">
          <div class="grid2">
            <a-form-item label="主机 IP" required>
              <a-input v-model:value="jobHostForm.ip" placeholder="10.0.0.10" />
            </a-form-item>
            <a-form-item label="SSH 端口">
              <a-input-number v-model:value="jobHostForm.port" :min="1" :max="65535" style="width: 100%" />
            </a-form-item>
            <a-form-item label="SSH 凭据" required>
              <a-select
                v-model:value="jobHostForm.credential_id"
                :options="credOptions"
                placeholder="选择凭据管理中的凭据"
                show-search
                option-filter-prop="label"
              />
            </a-form-item>
            <a-form-item label="工作目录">
              <a-input v-model:value="jobHostForm.workdir" placeholder="/tmp（临时文件上传与执行目录）" />
            </a-form-item>
          </div>
          <!-- 测试结果：成功显示 ansible 版本，失败显示原因 -->
          <a-alert
            v-if="testResult"
            :type="testResult.success ? 'success' : 'error'"
            :message="testResult.success ? `连通正常：${testResult.message}` : `连通失败：${testResult.message}`"
            show-icon
          />
          <div class="field-tip">未配置时含 Playbook 步骤的工单将执行失败；修改后对新派发的步骤生效。</div>
        </template>
        <div v-else class="disabled-tip">未配置：含 Ansible Playbook 步骤的工单将无法执行</div>
      </a-card>
      </div>
    </div>
  </a-spin>
</template>

<style scoped>
.settings {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 960px;
}
.block {
  border-radius: 14px;
}
.block-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
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
.field {
  padding: 14px 0;
  border-top: 1px solid var(--border);
}
.field-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-2);
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.field-tip {
  font-size: 12px;
  color: var(--text-3);
  margin-top: 8px;
}
.inline-item {
  font-size: 13px;
  color: var(--text-2);
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: 16px;
}
.grid2 :deep(.ant-form-item) {
  margin-bottom: 14px;
}
.disabled-tip {
  font-size: 13px;
  color: var(--text-3);
  padding: 6px 0;
}
</style>
