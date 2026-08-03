<script setup lang="ts">
// 登录页：四模式状态机（04-API设计 §2 登录三态）
//  password  账号密码（默认）
//  totp      已绑定 MFA，输入动态码（40103）
//  bind      required 策略未绑定，扫码绑定（40104）
//  changePwd 首次登录/重置后强制改密（40105）
// 另支持 OIDC 回调 ?ticket= 一次性换票（复用三态处理）
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import QRCode from 'qrcode'
import {
  CodeOutlined,
  FileDoneOutlined,
  GlobalOutlined,
  LeftOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons-vue'
import { ApiError } from '@/api/http'
import * as authApi from '@/api/auth'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

type Mode = 'password' | 'totp' | 'bind' | 'changePwd'
const mode = ref<Mode>('password')
const loading = ref(false)

const form = reactive({ username: '', password: '' })
const mfaForm = reactive({ code: '' })
const pwdForm = reactive({ oldPassword: '', newPassword: '', confirm: '' })

// 流程中间令牌（服务端签发，短时效）
let mfaToken = ''
let changeToken = ''

// MFA 绑定二维码
const qrDataUrl = ref('')
const totpSecret = ref('')

// SSO 选项（OIDC 未配置则隐藏按钮）
const oidcEnabled = ref(false)
const forgotPasswordEnabled = ref(false)

// 产品价值点：与 PRD 模块定位一致
const features = [
  { icon: SafetyCertificateOutlined, text: '操作全量审计留痕，安全合规有据可查' },
  { icon: FileDoneOutlined, text: '高危操作强制工单审批，杜绝误操作' },
  { icon: CodeOutlined, text: '作业模板化执行，实时日志全程可控' },
]

onMounted(async () => {
  // OIDC 回调携带一次性 ticket：立即换正式令牌（同样可能触发三态）
  const ticket = route.query.ticket as string | undefined
  if (ticket) {
    router.replace({ query: {} })
    await runFlow(() => authApi.ssoExchange(ticket))
  }
  try {
    const options = await authApi.fetchSsoOptions()
    oidcEnabled.value = options.oidc_enabled
    forgotPasswordEnabled.value = options.forgot_password_enabled
  } catch {
    /* 选项获取失败不影响密码登录 */
  }
})

/** 统一执行认证动作：成功进系统，三态错误切换对应模式 */
async function runFlow(action: () => Promise<authApi.TokenPair | null>) {
  loading.value = true
  try {
    const tokens = await action()
    if (tokens) await enterSystem(tokens)
  } catch (e) {
    if (e instanceof ApiError) {
      await onAuthChallenge(e)
    } else if (e instanceof Error) {
      // 非接口异常（二维码渲染等）也要可见，避免静默失败
      message.error(e.message || '操作失败，请重试')
    }
  } finally {
    loading.value = false
  }
}

/** 三态分支：40103 验证码 / 40104 绑定 / 40105 改密 */
async function onAuthChallenge(e: ApiError) {
  const data = (e.data ?? {}) as { mfa_token?: string; change_token?: string }
  if (e.code === 40103 && data.mfa_token) {
    mfaToken = data.mfa_token
    mfaForm.code = ''
    mode.value = 'totp'
  } else if (e.code === 40104 && data.mfa_token) {
    mfaToken = data.mfa_token
    mfaForm.code = ''
    mode.value = 'bind'
    await loadQrCode()
  } else if (e.code === 40105 && data.change_token) {
    changeToken = data.change_token
    // 40105 场景旧密码即刚输入的登录密码，预填减少重复输入
    pwdForm.oldPassword = form.password
    pwdForm.newPassword = ''
    pwdForm.confirm = ''
    mode.value = 'changePwd'
  }
}

/** 拉取 TOTP secret 并渲染二维码 */
async function loadQrCode() {
  const setup = await authApi.mfaSetup(mfaToken)
  totpSecret.value = setup.secret
  qrDataUrl.value = await QRCode.toDataURL(setup.otpauth_uri, { width: 180, margin: 1 })
}

/** 登录成功：保存令牌 -> 拉用户信息 -> 跳转 */
async function enterSystem(tokens: authApi.TokenPair) {
  userStore.applyTokens(tokens)
  await userStore.loadUserInfo()
  message.success('登录成功')
  router.push((route.query.redirect as string) || '/')
}

function onSubmitPassword() {
  if (!form.username || !form.password) {
    message.warning('请输入账号和密码')
    return
  }
  runFlow(() => authApi.login(form.username, form.password))
}

function onSubmitTotp() {
  if (mfaForm.code.length !== 6) {
    message.warning('请输入 6 位动态码')
    return
  }
  runFlow(() => authApi.mfaVerify(mfaToken, mfaForm.code))
}

function onSubmitBind() {
  if (mfaForm.code.length !== 6) {
    message.warning('请输入认证器生成的 6 位动态码')
    return
  }
  runFlow(() => authApi.mfaBind(mfaForm.code, mfaToken))
}

function onSubmitChangePwd() {
  if (!pwdForm.newPassword || pwdForm.newPassword !== pwdForm.confirm) {
    message.warning('两次输入的新密码不一致')
    return
  }
  runFlow(() => authApi.changePassword(pwdForm.oldPassword, pwdForm.newPassword, changeToken))
}

/** 返回账号密码模式（中间令牌作废，重新登录） */
function backToPassword() {
  mode.value = 'password'
  mfaToken = ''
  changeToken = ''
  qrDataUrl.value = ''
}

/** OIDC SSO：整页跳转 IdP 授权（非 XHR） */
function onOidcLogin() {
  window.location.href = '/api/v1/auth/sso/oidc/login'
}
</script>

<template>
  <div class="login-page">
    <!-- 左侧品牌区 -->
    <div class="brand">
      <div class="glow a"></div>
      <div class="glow b"></div>
      <div class="ring a"></div>
      <div class="ring b"></div>
      <div class="brand-inner">
        <div class="brand-logo">
          <img class="ic" src="/logo.svg" alt="OpsPilot" />
          <b>OpsPilot</b>
        </div>
        <h1>让运维操作<br /><span class="hl">标准化 · 可审批 · 可审计</span></h1>
        <p>
          面向中小团队的自动化运维平台，覆盖资源管理、作业执行、工单审批与安全审计的完整闭环。
        </p>
        <div class="feat">
          <div v-for="f in features" :key="f.text" class="feat-item">
            <div class="fi"><component :is="f.icon" /></div>
            {{ f.text }}
          </div>
        </div>
      </div>
    </div>

    <!-- 右侧表单区 -->
    <div class="form-side">
      <div class="form-card">
        <!-- 模式一：账号密码 -->
        <template v-if="mode === 'password'">
          <h2>欢迎回来</h2>
          <div class="sub">登录 OpsPilot 运维平台，开始高效运维</div>
          <a-form :model="form" layout="vertical" @finish="onSubmitPassword">
            <a-form-item label="账号" name="username">
              <a-input
                v-model:value="form.username"
                placeholder="本地账号或 LDAP 域账号"
                size="large"
              />
            </a-form-item>
            <a-form-item label="密码" name="password">
              <a-input-password
                v-model:value="form.password"
                placeholder="请输入密码"
                size="large"
              />
            </a-form-item>
            <a-button
              type="primary"
              html-type="submit"
              size="large"
              block
              class="login-btn"
              :loading="loading"
            >
              登 录
            </a-button>
            <div v-if="forgotPasswordEnabled" class="forgot-link">
              <a @click="router.push('/forgot-password')">忘记密码？</a>
            </div>
          </a-form>
          <template v-if="oidcEnabled">
            <div class="divider">其他登录方式</div>
            <div class="sso-row">
              <button class="sso" @click="onOidcLogin"><GlobalOutlined />OIDC 单点登录</button>
            </div>
          </template>
        </template>

        <!-- 模式二：MFA 动态码验证（40103） -->
        <template v-else-if="mode === 'totp'">
          <h2>安全验证</h2>
          <div class="sub">请输入认证器 App 中的 6 位动态码</div>
          <a-form layout="vertical" :model="mfaForm" @finish="onSubmitTotp">
            <a-form-item label="动态码">
              <a-input
                v-model:value="mfaForm.code"
                placeholder="6 位数字"
                size="large"
                :maxlength="6"
                autofocus
              />
            </a-form-item>
            <a-button
              type="primary"
              html-type="submit"
              size="large"
              block
              class="login-btn"
              :loading="loading"
            >
              验 证
            </a-button>
          </a-form>
          <a class="back-link" @click="backToPassword"><LeftOutlined /> 返回重新登录</a>
        </template>

        <!-- 模式三：MFA 首次绑定（40104） -->
        <template v-else-if="mode === 'bind'">
          <h2>绑定 MFA 认证器</h2>
          <div class="sub">平台要求启用双因素认证，请使用认证器 App 扫码绑定</div>
          <div class="qr-box">
            <img v-if="qrDataUrl" :src="qrDataUrl" alt="MFA 绑定二维码" />
            <a-spin v-else />
          </div>
          <div class="secret-hint">
            无法扫码？手动输入密钥：<code>{{ totpSecret }}</code>
          </div>
          <a-form layout="vertical" :model="mfaForm" @finish="onSubmitBind">
            <a-form-item label="动态码">
              <a-input
                v-model:value="mfaForm.code"
                placeholder="输入认证器生成的 6 位数字完成绑定"
                size="large"
                :maxlength="6"
              />
            </a-form-item>
            <a-button
              type="primary"
              html-type="submit"
              size="large"
              block
              class="login-btn"
              :loading="loading"
            >
              完成绑定
            </a-button>
          </a-form>
          <a class="back-link" @click="backToPassword"><LeftOutlined /> 返回重新登录</a>
        </template>

        <!-- 模式四：强制修改初始密码（40105） -->
        <template v-else>
          <h2>修改初始密码</h2>
          <div class="sub">首次登录（或密码被重置）须设置新密码后才能进入系统</div>
          <a-form layout="vertical" :model="pwdForm" @finish="onSubmitChangePwd">
            <a-form-item label="当前密码">
              <a-input-password v-model:value="pwdForm.oldPassword" size="large" />
            </a-form-item>
            <a-form-item label="新密码">
              <a-input-password
                v-model:value="pwdForm.newPassword"
                placeholder="至少 8 位，包含字母与数字"
                size="large"
              />
            </a-form-item>
            <a-form-item label="确认新密码">
              <a-input-password v-model:value="pwdForm.confirm" size="large" />
            </a-form-item>
            <a-button
              type="primary"
              html-type="submit"
              size="large"
              block
              class="login-btn"
              :loading="loading"
            >
              修改并登录
            </a-button>
          </a-form>
          <a class="back-link" @click="backToPassword"><LeftOutlined /> 返回重新登录</a>
        </template>

        <div class="foot">© 2026 OpsPilot · V1.0</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  min-height: 100vh;
}

/* ===== 左侧品牌区 ===== */
.brand {
  flex: 1.2;
  position: relative;
  overflow: hidden;
  color: #fff;
  background: linear-gradient(145deg, #060b18 0%, #0f1d45 50%, #1d4ed8 130%);
  display: flex;
  flex-direction: column;
  justify-content: center;
  /* 内容块在蓝色背景中水平居中（块内文本仍左对齐） */
  align-items: center;
  padding: 72px;
}
/* 网格纹理 */
.brand::before {
  content: '';
  position: absolute;
  inset: 0;
  background-image: linear-gradient(rgba(255, 255, 255, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.05) 1px, transparent 1px);
  background-size: 44px 44px;
}
.glow {
  position: absolute;
  border-radius: 50%;
  filter: blur(70px);
}
.glow.a {
  width: 420px;
  height: 420px;
  background: rgba(37, 99, 235, 0.35);
  top: -120px;
  right: -100px;
}
.glow.b {
  width: 340px;
  height: 340px;
  background: rgba(30, 64, 175, 0.4);
  bottom: -100px;
  left: -80px;
}
.ring {
  position: absolute;
  border: 1.5px solid rgba(255, 255, 255, 0.14);
  border-radius: 50%;
}
.ring.a {
  width: 300px;
  height: 300px;
  bottom: 8%;
  right: 6%;
}
.ring.b {
  width: 180px;
  height: 180px;
  bottom: 16%;
  right: 13%;
  border-style: dashed;
}
.brand-inner {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 480px;
}
.brand-logo {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 48px;
}
.brand-logo .ic {
  width: 44px;
  height: 44px;
  border-radius: 13px;
  border: 1px solid rgba(255, 255, 255, 0.25);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.18);
}
.brand-logo b {
  font-size: 20px;
  letter-spacing: 0.3px;
}
.brand h1 {
  font-size: 34px;
  line-height: 1.35;
  font-weight: 800;
  margin: 0 0 16px;
  color: #fff;
}
.brand .hl {
  color: #93c5fd;
}
.brand p {
  font-size: 15px;
  line-height: 1.8;
  color: rgba(255, 255, 255, 0.75);
  margin: 0 0 44px;
}
.feat {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.feat-item {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
  color: rgba(255, 255, 255, 0.9);
}
.feat-item .fi {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.14);
  border: 1px solid rgba(255, 255, 255, 0.2);
  color: #bfdbfe;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ===== 右侧表单区 ===== */
.form-side {
  flex: 1;
  background: linear-gradient(145deg, #f8fafc 0%, #eef2ff 52%, #ecfeff 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}
.form-card {
  position: relative;
  overflow: hidden;
  width: 400px;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(99, 102, 241, 0.2);
  border-radius: 22px;
  padding: 42px 40px 34px;
  box-shadow: 0 20px 50px rgba(30, 41, 99, 0.16), 0 4px 14px rgba(14, 116, 144, 0.08);
}
.form-card::before {
  content: '';
  position: absolute;
  inset: 0 0 auto;
  height: 5px;
  background: linear-gradient(90deg, #06b6d4, #2563eb 48%, #7c3aed);
}
.form-card h2 {
  font-size: 24px;
  font-weight: 800;
  margin: 0 0 10px;
  color: #111827;
  letter-spacing: -0.2px;
}
.form-card .sub {
  font-size: 13px;
  color: var(--text-3);
  margin-bottom: 28px;
}
/* 表单标签（账号/密码等）加大加粗，提升可读性 */
.form-card :deep(.ant-form-item-label > label) {
  font-size: 15px;
  font-weight: 700;
  color: #334155;
}
.form-card :deep(.ant-input),
.form-card :deep(.ant-input-affix-wrapper) {
  border-color: #dbe4f0;
  background: #f8fafc;
  border-radius: 10px;
  transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
}
.form-card :deep(.ant-input:hover),
.form-card :deep(.ant-input-affix-wrapper:hover),
.form-card :deep(.ant-input:focus),
.form-card :deep(.ant-input-affix-wrapper-focused) {
  border-color: #06b6d4;
  background: #fff;
  box-shadow: 0 0 0 3px rgba(6, 182, 212, 0.14);
}
.login-btn {
  height: 46px;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 1px;
  border-radius: 10px;
  background: linear-gradient(100deg, #2563eb, #4f46e5 55%, #7c3aed);
  border: 0;
  box-shadow: 0 10px 20px rgba(79, 70, 229, 0.24);
  margin-top: 4px;
  transition: transform 0.2s, box-shadow 0.2s, filter 0.2s;
}
.login-btn:hover {
  filter: saturate(1.12) brightness(1.05);
  transform: translateY(-1px);
  box-shadow: 0 13px 24px rgba(79, 70, 229, 0.3);
}
.forgot-link {
  text-align: right;
  margin-top: 12px;
  font-size: 13px;
}
.divider {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 24px 0 16px;
  color: var(--text-3);
  font-size: 12px;
}
.divider::before,
.divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--border);
}
.sso-row {
  display: flex;
  gap: 12px;
}
.sso {
  flex: 1;
  height: 40px;
  border-radius: 10px;
  border: 1.5px solid var(--border);
  background: var(--bg-input);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-2);
  font-weight: 600;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.sso:hover {
  border-color: var(--primary);
  color: var(--primary);
}

/* ===== MFA 绑定 / 返回链接 ===== */
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
.qr-box img {
  display: block;
  border-radius: 8px;
}
.secret-hint {
  font-size: 12px;
  color: var(--text-3);
  margin-bottom: 16px;
  word-break: break-all;
}
.secret-hint code {
  color: var(--text-2);
  background: var(--bg-hover);
  padding: 2px 6px;
  border-radius: 6px;
}
.back-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 18px;
  font-size: 13px;
  color: var(--text-3);
  cursor: pointer;
}
.back-link:hover {
  color: var(--primary);
}
.foot {
  text-align: center;
  font-size: 12px;
  color: var(--text-3);
  margin-top: 26px;
}

/* 窄屏降级：隐藏品牌区，仅保留表单 */
@media (max-width: 900px) {
  .brand {
    display: none;
  }
  .form-side {
    padding: 24px 16px;
  }
  .form-card {
    width: min(400px, 100%);
  }
}
@media (prefers-reduced-motion: reduce) {
  .login-btn,
  .form-card :deep(.ant-input),
  .form-card :deep(.ant-input-affix-wrapper) {
    transition: none;
  }
}
</style>
