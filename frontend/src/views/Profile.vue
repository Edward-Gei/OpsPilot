<script setup lang="ts">
// 个人中心：账号资料（显示名/邮箱自助修改）/ 修改密码（access 通道）/ MFA 自助绑定（冲突决策④）
import { computed, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import QRCode from 'qrcode'
import { SafetyCertificateOutlined } from '@ant-design/icons-vue'
import * as authApi from '@/api/auth'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const me = computed(() => userStore.userInfo)

const sourceText: Record<string, string> = { local: '本地账号', ldap: 'LDAP', oidc: 'OIDC SSO' }

// ---------- 资料修改（仅显示名/邮箱） ----------
const profileForm = reactive({ display_name: '', email: '' })
const profileLoading = ref(false)

// 用户信息就绪/刷新后回填表单
watch(
  me,
  (v) => {
    if (v) {
      profileForm.display_name = v.display_name
      profileForm.email = v.email || ''
    }
  },
  { immediate: true },
)

/** 保存资料：成功后刷新用户信息（顶栏头像/名称同步更新） */
async function onSaveProfile() {
  if (!profileForm.display_name.trim()) {
    message.warning('显示名不能为空')
    return
  }
  profileLoading.value = true
  try {
    await authApi.updateProfile(profileForm.display_name.trim(), profileForm.email.trim() || null)
    message.success('资料已保存')
    await userStore.loadUserInfo()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    profileLoading.value = false
  }
}

// ---------- 修改密码 ----------
const pwdForm = reactive({ oldPassword: '', newPassword: '', confirm: '' })
const pwdLoading = ref(false)

/** 个人中心改密：成功后令牌不变，仅提示 */
async function onChangePassword() {
  if (!pwdForm.oldPassword || !pwdForm.newPassword) {
    message.warning('请填写完整')
    return
  }
  if (pwdForm.newPassword !== pwdForm.confirm) {
    message.warning('两次输入的新密码不一致')
    return
  }
  pwdLoading.value = true
  try {
    await authApi.changePassword(pwdForm.oldPassword, pwdForm.newPassword)
    message.success('密码修改成功')
    pwdForm.oldPassword = pwdForm.newPassword = pwdForm.confirm = ''
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    pwdLoading.value = false
  }
}

// ---------- MFA 自助绑定 ----------
const bindVisible = ref(false)
const qrDataUrl = ref('')
const totpSecret = ref('')
const bindCode = ref('')
const bindLoading = ref(false)

/** 打开绑定弹窗并拉取二维码（access token 通道，不传 mfa_token） */
async function openBind() {
  bindVisible.value = true
  qrDataUrl.value = ''
  bindCode.value = ''
  const setup = await authApi.mfaSetup()
  totpSecret.value = setup.secret
  qrDataUrl.value = await QRCode.toDataURL(setup.otpauth_uri, { width: 180, margin: 1 })
}

/** 回填动态码完成绑定，刷新用户信息 */
async function onBind() {
  if (bindCode.value.length !== 6) {
    message.warning('请输入 6 位动态码')
    return
  }
  bindLoading.value = true
  try {
    await authApi.mfaBind(bindCode.value)
    message.success('MFA 绑定成功')
    bindVisible.value = false
    await userStore.loadUserInfo()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    bindLoading.value = false
  }
}
</script>

<template>
  <div v-if="me" class="profile">
    <!-- 账号资料 -->
    <a-card title="账号资料" class="card">
      <a-descriptions :column="2" bordered size="middle">
        <a-descriptions-item label="用户名">{{ me.username }}</a-descriptions-item>
        <a-descriptions-item label="显示名">
          <a-input
            v-model:value="profileForm.display_name"
            :maxlength="64"
            placeholder="显示名"
            class="profile-input"
          />
        </a-descriptions-item>
        <a-descriptions-item label="邮箱">
          <a-input
            v-model:value="profileForm.email"
            :maxlength="128"
            placeholder="用于接收平台通知，可留空"
            class="profile-input"
          />
        </a-descriptions-item>
        <a-descriptions-item label="账号来源">
          {{ sourceText[me.source] || me.source }}
        </a-descriptions-item>
        <a-descriptions-item label="角色">
          <a-tag v-for="r in me.roles" :key="r.id" color="blue">{{ r.name }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="最近登录">
          {{ me.last_login_at ? new Date(me.last_login_at).toLocaleString() : '—' }}
        </a-descriptions-item>
      </a-descriptions>
      <div class="profile-actions">
        <a-button type="primary" :loading="profileLoading" @click="onSaveProfile">保存</a-button>
      </div>
    </a-card>

    <!-- MFA 双因素认证 -->
    <a-card title="双因素认证（MFA）" class="card">
      <div class="mfa-row">
        <div class="mfa-info">
          <SafetyCertificateOutlined class="mfa-icon" :class="{ on: me.mfa_enabled }" />
          <div>
            <b>{{ me.mfa_enabled ? '已启用' : '未启用' }}</b>
            <div class="mfa-desc">
              绑定后登录需额外输入认证器动态码，显著提升账号安全性
            </div>
          </div>
        </div>
        <a-button v-if="!me.mfa_enabled" type="primary" @click="openBind">立即绑定</a-button>
        <a-tag v-else color="success">保护中</a-tag>
      </div>
    </a-card>

    <!-- 修改密码（SSO 账号密码由 IdP 托管，不展示） -->
    <a-card v-if="me.source === 'local' || me.source === 'ldap'" title="修改密码" class="card">
      <template v-if="me.source === 'ldap'">
        <a-alert type="info" show-icon message="LDAP 账号密码由域控统一管理，请联系管理员修改。" />
      </template>
      <a-form v-else layout="vertical" class="pwd-form" :model="pwdForm" @finish="onChangePassword">
        <a-form-item label="当前密码">
          <a-input-password v-model:value="pwdForm.oldPassword" />
        </a-form-item>
        <a-form-item label="新密码">
          <a-input-password
            v-model:value="pwdForm.newPassword"
            placeholder="至少 8 位，包含字母与数字"
          />
        </a-form-item>
        <a-form-item label="确认新密码">
          <a-input-password v-model:value="pwdForm.confirm" />
        </a-form-item>
        <a-button type="primary" html-type="submit" :loading="pwdLoading">保存修改</a-button>
      </a-form>
    </a-card>

    <!-- MFA 绑定弹窗 -->
    <a-modal v-model:open="bindVisible" title="绑定 MFA 认证器" :footer="null" width="420px">
      <div class="qr-box">
        <img v-if="qrDataUrl" :src="qrDataUrl" alt="MFA 绑定二维码" />
        <a-spin v-else />
      </div>
      <div class="secret-hint">
        无法扫码？手动输入密钥：<code>{{ totpSecret }}</code>
      </div>
      <a-input
        v-model:value="bindCode"
        placeholder="输入认证器生成的 6 位动态码"
        :maxlength="6"
        size="large"
        class="bind-input"
      />
      <a-button type="primary" block size="large" :loading="bindLoading" @click="onBind">
        完成绑定
      </a-button>
    </a-modal>
  </div>
</template>

<style scoped>
.profile {
  max-width: 860px;
}
.card {
  margin-bottom: 20px;
  border-radius: 14px;
}
.profile-input {
  max-width: 260px;
}
.profile-actions {
  margin-top: 16px;
  display: flex;
  justify-content: flex-start;
}
.mfa-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.mfa-info {
  display: flex;
  align-items: center;
  gap: 14px;
}
.mfa-icon {
  font-size: 30px;
  color: var(--text-3);
}
.mfa-icon.on {
  color: var(--success, #16a34a);
}
.mfa-desc {
  font-size: 12px;
  color: var(--text-3);
  margin-top: 5px;
}
.pwd-form {
  max-width: 360px;
}
.qr-box {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 190px;
  margin-bottom: 12px;
  border: 1px dashed var(--border);
  border-radius: 12px;
  background: #fff;
}
.secret-hint {
  font-size: 12px;
  color: var(--text-3);
  margin-bottom: 16px;
  word-break: break-all;
}
.secret-hint code {
  background: var(--bg-hover);
  padding: 2px 6px;
  border-radius: 6px;
}
.bind-input {
  margin-bottom: 14px;
}
</style>
