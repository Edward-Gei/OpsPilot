<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  CodeOutlined,
  FileDoneOutlined,
  LeftOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons-vue'
import * as authApi from '@/api/auth'

const route = useRoute()
const router = useRouter()
const token = String(route.query.token || '')
const status = ref<'loading' | 'valid' | 'expired' | 'used' | 'invalid'>('loading')
const loading = ref(false)
const policy = ref<{ min_length: number; require_complex: boolean } | null>(null)
const form = reactive({ newPassword: '', confirm: '' })

const features = [
  { icon: SafetyCertificateOutlined, text: '操作全量审计留痕，安全合规有据可查' },
  { icon: FileDoneOutlined, text: '高危操作强制工单审批，杜绝误操作' },
  { icon: CodeOutlined, text: '作业模板化执行，实时日志全程可控' },
]

onMounted(async () => {
  if (!token) {
    status.value = 'invalid'
    return
  }
  try {
    const [validation, pwdPolicy] = await Promise.all([
      authApi.validateResetToken(token),
      authApi.fetchPasswordPolicy(),
    ])
    status.value = validation.status
    policy.value = pwdPolicy
  } catch {
    status.value = 'invalid'
  }
})

async function submit() {
  if (loading.value) return
  if (form.newPassword !== form.confirm) {
    message.warning('两次输入的新密码不一致')
    return
  }
  loading.value = true
  try {
    await authApi.resetPassword(token, form.newPassword)
    message.success('密码重置成功，请使用新密码登录。')
    await router.push('/login')
  } catch {
    // The shared response interceptor renders API and network errors.
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <aside class="brand">
      <div class="brand-inner">
        <div class="brand-logo">
          <img class="ic" src="/logo.svg" alt="OpsPilot" />
          <b>OpsPilot</b>
        </div>
        <h1>让运维操作<br /><span class="hl">标准化 · 可审计 · 可追溯</span></h1>
        <p>面向中小团队的自动化运维平台，覆盖资源管理、作业执行、工单审批与安全审计的完整闭环。</p>
        <div class="feat">
          <div v-for="feature in features" :key="feature.text" class="feat-item">
            <div class="fi"><component :is="feature.icon" /></div>
            {{ feature.text }}
          </div>
        </div>
      </div>
    </aside>

    <main class="form-side">
      <section class="form-card" aria-live="polite">
        <template v-if="status === 'loading'">
          <div class="state-block loading-state">
            <a-spin size="large" />
            <h2>正在验证链接</h2>
            <p>请稍候，系统正在确认重置链接的有效性。</p>
          </div>
        </template>

        <template v-else-if="status === 'valid'">
          <h2>重置密码</h2>
          <p class="sub">请设置一个仅用于 OpsPilot 的新密码。</p>
          <div v-if="policy" class="policy">
            <SafetyCertificateOutlined />
            <span>密码至少 {{ policy.min_length }} 位{{ policy.require_complex ? '，且包含字母和数字' : '' }}。</span>
          </div>
          <a-form :model="form" layout="vertical" @finish="submit">
            <a-form-item label="新密码" name="newPassword">
              <a-input-password v-model:value="form.newPassword" size="large" autocomplete="new-password" />
            </a-form-item>
            <a-form-item label="确认新密码" name="confirm">
              <a-input-password v-model:value="form.confirm" size="large" autocomplete="new-password" />
            </a-form-item>
            <a-button
              type="primary"
              html-type="submit"
              block
              size="large"
              class="login-btn"
              :loading="loading"
              :disabled="loading"
            >
              确认重置密码
            </a-button>
          </a-form>
        </template>

        <template v-else-if="status === 'expired'">
          <div class="state-block">
            <div class="state-icon"><SafetyCertificateOutlined /></div>
            <h2>链接已过期</h2>
            <p>该重置链接已过期，请重新申请。</p>
            <a-button type="primary" block size="large" class="login-btn" @click="router.push('/forgot-password')">重新申请</a-button>
          </div>
        </template>

        <template v-else-if="status === 'used'">
          <div class="state-block">
            <div class="state-icon"><FileDoneOutlined /></div>
            <h2>链接已使用</h2>
            <p>该重置链接已使用，请使用新密码登录。</p>
            <a-button type="primary" block size="large" class="login-btn" @click="router.push('/login')">返回登录</a-button>
          </div>
        </template>

        <template v-else>
          <div class="state-block">
            <div class="state-icon"><SafetyCertificateOutlined /></div>
            <h2>链接无效</h2>
            <p>该重置链接无效，请重新申请。</p>
            <a-button type="primary" block size="large" class="login-btn" @click="router.push('/forgot-password')">重新申请</a-button>
          </div>
        </template>

        <a class="back-link" @click="router.push('/login')"><LeftOutlined /> 返回登录</a>
        <div class="foot">© 2026 OpsPilot · V1.0</div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.login-page { display: flex; min-height: 100vh; }
.brand { flex: 1.2; position: relative; overflow: hidden; color: #fff; background: linear-gradient(145deg, #060b18 0%, #0f1d45 50%, #1d4ed8 130%); display: flex; align-items: center; padding: 72px; }
.brand::before { content: ''; position: absolute; inset: 0; background-image: linear-gradient(rgba(255,255,255,.05) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.05) 1px, transparent 1px); background-size: 44px 44px; }
.brand-inner { position: relative; z-index: 1; width: 100%; max-width: 480px; margin: auto; }
.brand-logo { display: flex; align-items: center; gap: 12px; margin-bottom: 48px; }
.brand-logo .ic { width: 44px; height: 44px; border-radius: 13px; border: 1px solid rgba(255,255,255,.25); box-shadow: 0 4px 12px rgba(0,0,0,.18); }
.brand-logo b { font-size: 20px; letter-spacing: .3px; }
.brand h1 { margin: 0 0 16px; color: #fff; font-size: 34px; font-weight: 800; line-height: 1.35; }
.brand .hl { color: #93c5fd; }
.brand p { margin: 0 0 44px; color: rgba(255,255,255,.75); font-size: 15px; line-height: 1.8; }
.feat { display: flex; flex-direction: column; gap: 16px; }
.feat-item { display: flex; align-items: center; gap: 12px; color: rgba(255,255,255,.9); font-size: 14px; }
.fi { display: flex; flex: 0 0 30px; align-items: center; justify-content: center; width: 30px; height: 30px; border: 1px solid rgba(255,255,255,.2); border-radius: 9px; background: rgba(255,255,255,.14); color: #bfdbfe; }
.form-side { display: flex; flex: 1; align-items: center; justify-content: center; padding: 40px; background: linear-gradient(145deg, #f8fafc 0%, #eef2ff 52%, #ecfeff 100%); }
.form-card { position: relative; width: min(400px, 100%); overflow: hidden; padding: 42px 40px 34px; border: 1px solid rgba(99,102,241,.2); border-radius: 22px; background: rgba(255,255,255,.94); box-shadow: 0 20px 50px rgba(30,41,99,.16), 0 4px 14px rgba(14,116,144,.08); }
.form-card::before { position: absolute; inset: 0 0 auto; height: 5px; background: linear-gradient(90deg, #06b6d4, #2563eb 48%, #7c3aed); content: ''; }
h2 { margin: 0 0 10px; color: #111827; font-size: 24px; font-weight: 800; line-height: 1.35; }
.sub { margin: 0 0 24px; color: var(--text-3); font-size: 13px; line-height: 1.7; }
.policy { display: flex; gap: 9px; align-items: flex-start; margin: 0 0 24px; padding: 10px 12px; border: 1px solid #bfdbfe; border-radius: 10px; background: #eff6ff; color: #1e40af; font-size: 13px; line-height: 1.6; }
.policy :deep(.anticon) { margin-top: 3px; }
.form-card :deep(.ant-form-item-label > label) { color: #334155; font-weight: 700; }
.form-card :deep(.ant-input), .form-card :deep(.ant-input-affix-wrapper) { border-color: #dbe4f0; border-radius: 10px; background: #f8fafc; transition: border-color .2s, box-shadow .2s, background .2s; }
.form-card :deep(.ant-input:hover), .form-card :deep(.ant-input-affix-wrapper:hover), .form-card :deep(.ant-input:focus), .form-card :deep(.ant-input-affix-wrapper-focused) { border-color: #06b6d4; background: #fff; box-shadow: 0 0 0 3px rgba(6,182,212,.14); }
.login-btn { height: 48px; margin-top: 4px; border: 0; border-radius: 10px; background: linear-gradient(100deg, #2563eb, #4f46e5 55%, #7c3aed); box-shadow: 0 10px 20px rgba(79,70,229,.24); font-weight: 800; letter-spacing: 0; transition: transform .2s, box-shadow .2s, filter .2s; }
.login-btn:not(:disabled):hover { box-shadow: 0 13px 24px rgba(79,70,229,.3); filter: saturate(1.12) brightness(1.05); transform: translateY(-1px); }
.state-block { display: flex; min-height: 220px; flex-direction: column; align-items: center; justify-content: center; text-align: center; }
.state-block p { margin: 0 0 24px; color: var(--text-3); line-height: 1.7; }
.state-block .login-btn { width: 100%; }
.loading-state { min-height: 190px; }
.loading-state h2 { margin-top: 24px; }
.state-icon { display: flex; align-items: center; justify-content: center; width: 52px; height: 52px; margin-bottom: 22px; border-radius: 16px; background: #eef2ff; color: #4f46e5; font-size: 24px; }
.back-link { display: flex; align-items: center; justify-content: center; gap: 7px; min-height: 44px; margin-top: 18px; color: #2563eb; font-weight: 600; }
.back-link:hover { color: #7c3aed; }
.foot { margin-top: 16px; color: var(--text-3); font-size: 12px; text-align: center; }

@media (max-width: 900px) { .brand { display: none; } .form-side { padding: 24px 16px; } .form-card { padding: 38px 28px 30px; } }
@media (prefers-reduced-motion: reduce) { .form-card :deep(.ant-input), .form-card :deep(.ant-input-affix-wrapper), .login-btn { transition: none; } }
</style>
