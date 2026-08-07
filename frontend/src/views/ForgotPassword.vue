<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { CodeOutlined, FileDoneOutlined, SafetyCertificateOutlined } from '@ant-design/icons-vue'
import * as authApi from '@/api/auth'

const router = useRouter()
const form = reactive({ email: '' })
const loading = ref(false)
const checking = ref(true)
const enabled = ref(false)
const submitted = ref(false)
const universalMessage = '如果该邮箱已绑定本地账号，系统将发送密码重置邮件，请查收邮箱。'

onMounted(async () => {
  try {
    enabled.value = (await authApi.fetchSsoOptions()).forgot_password_enabled
  } catch {
    enabled.value = false
  } finally {
    checking.value = false
  }
})

async function submit() {
  if (loading.value) return
  if (!form.email.trim() || !form.email.includes('@')) {
    message.warning('请输入有效邮箱')
    return
  }
  loading.value = true
  try {
    await authApi.forgotPassword(form.email.trim())
    submitted.value = true
  } catch {
    message.error('提交失败，请稍后重试')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="brand">
      <div class="brand-inner">
        <div class="brand-logo"><img class="ic" src="/logo.svg" alt="OpsPilot" /><b>OpsPilot</b></div>
        <h1>让运维操作<br /><span class="hl">标准化 · 可审批 · 可审计</span></h1>
        <p>面向中小团队的自动化运维平台，覆盖资源管理、作业执行、工单审批与安全审计的完整闭环。</p>
        <div class="feat">
          <div class="feat-item"><div class="fi"><SafetyCertificateOutlined /></div>操作全量审计留痕，安全合规有据可查</div>
          <div class="feat-item"><div class="fi"><FileDoneOutlined /></div>高危操作强制工单审批，杜绝误操作</div>
          <div class="feat-item"><div class="fi"><CodeOutlined /></div>作业模板化执行，实时日志全程可控</div>
        </div>
      </div>
    </div>
    <div class="form-side">
      <div class="form-card" :class="{ 'is-checking': checking }">
        <div v-if="checking" class="capability-loading"><a-spin size="small" /><span>正在检查邮件服务...</span></div>
        <h2>找回密码</h2>
        <div class="sub">通过已绑定邮箱申请密码重置</div>
        <p v-if="!enabled" class="system-hint">当前系统未配置密码重置邮件服务，请联系管理员重置密码。</p>
        <template v-else-if="!submitted">
          <a-form :model="form" layout="vertical" @finish="submit">
          <a-form-item label="邮箱">
              <a-input v-model:value="form.email" size="large" placeholder="请输入已绑定的邮箱" />
          </a-form-item>
            <a-button type="primary" html-type="submit" block size="large" :loading="loading" @click="submit">发送重置邮件</a-button>
          </a-form>
        </template>
        <p v-else class="result">{{ universalMessage }}</p>
        <a class="back" @click="router.push('/login')">返回登录</a>
        <div class="foot">© 2026 OpsPilot · V1.0</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page { display: flex; min-height: 100vh; }
.brand { flex: 1.2; position: relative; overflow: hidden; color: #fff; background: linear-gradient(145deg, #090b1c 0%, #172554 48%, #0e7490 100%); display: flex; align-items: center; padding: 72px; }
.brand::before { content: ''; position: absolute; inset: 0; background-image: linear-gradient(rgba(255,255,255,.05) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.05) 1px, transparent 1px); background-size: 44px 44px; }
.brand-inner { position: relative; z-index: 1; width: 100%; max-width: 480px; margin: auto; }
.brand-logo { display: flex; align-items: center; gap: 12px; margin-bottom: 48px; }
.brand-logo .ic { width: 44px; height: 44px; border-radius: 13px; border: 1px solid rgba(255,255,255,.25); }
.brand-logo b { font-size: 20px; }
.brand h1 { font-size: 34px; line-height: 1.35; font-weight: 800; margin: 0 0 16px; color: #fff; }
.brand .hl { color: #93c5fd; }
.brand p { font-size: 15px; line-height: 1.8; color: rgba(255,255,255,.75); margin: 0 0 44px; }
.feat { display: flex; flex-direction: column; gap: 16px; }
.feat-item { display: flex; align-items: center; gap: 12px; font-size: 14px; color: rgba(255,255,255,.9); }
.fi { width: 30px; height: 30px; border-radius: 9px; flex-shrink: 0; background: rgba(255,255,255,.14); border: 1px solid rgba(255,255,255,.2); color: #bfdbfe; display: flex; align-items: center; justify-content: center; }
.form-side { flex: 1; background: linear-gradient(145deg, #f8fafc 0%, #eef2ff 52%, #ecfeff 100%); display: flex; align-items: center; justify-content: center; padding: 40px; }
.form-card { position: relative; overflow: hidden; width: 400px; background: rgba(255,255,255,.94); border: 1px solid rgba(99,102,241,.2); border-radius: 22px; padding: 42px 40px 34px; box-shadow: 0 20px 50px rgba(30,41,99,.16), 0 4px 14px rgba(14,116,144,.08); }
.form-card.is-checking > :not(.capability-loading) { visibility: hidden; }
.capability-loading { min-height: 88px; display: flex; align-items: center; justify-content: center; gap: 10px; color: var(--text-3); font-size: 13px; }
.form-card::before { content: ''; position: absolute; inset: 0 0 auto; height: 5px; background: linear-gradient(90deg, #06b6d4, #2563eb 48%, #7c3aed); }
.form-card h2 { font-size: 24px; font-weight: 800; margin: 0 0 10px; color: #111827; letter-spacing: -.2px; }
.sub, .system-hint, .result { color: var(--text-3); line-height: 1.7; }
.sub { font-size: 13px; margin-bottom: 28px; }
.system-hint, .result { margin: 20px 0; }
.form-card :deep(.ant-form-item-label > label) { color: #334155; font-weight: 700; }
.form-card :deep(.ant-input), .form-card :deep(.ant-input-affix-wrapper) { border-color: #dbe4f0; background: #f8fafc; border-radius: 10px; transition: border-color .2s, box-shadow .2s, background .2s; }
.form-card :deep(.ant-input:hover), .form-card :deep(.ant-input-affix-wrapper:hover), .form-card :deep(.ant-input:focus), .form-card :deep(.ant-input-affix-wrapper-focused) { border-color: #06b6d4; background: #fff; box-shadow: 0 0 0 3px rgba(6,182,212,.14); }
.login-btn { height: 48px; margin-top: 4px; border: 0; border-radius: 10px; font-weight: 800; letter-spacing: 1px; background: linear-gradient(100deg, #2563eb, #4f46e5 55%, #7c3aed); box-shadow: 0 10px 20px rgba(79,70,229,.24); transition: transform .2s, box-shadow .2s, filter .2s; }
.login-btn:hover { filter: saturate(1.12) brightness(1.05); transform: translateY(-1px); box-shadow: 0 13px 24px rgba(79,70,229,.3); }
.back { display: block; margin-top: 22px; text-align: center; color: #2563eb; font-weight: 600; }
.back:hover { color: #7c3aed; }
.foot { text-align: center; font-size: 12px; color: var(--text-3); margin-top: 26px; }

@media (max-width: 900px) { .brand { display: none; } .form-side { padding: 24px 16px; } .form-card { width: min(400px, 100%); } }
@media (prefers-reduced-motion: reduce) { .login-btn, .form-card :deep(.ant-input), .form-card :deep(.ant-input-affix-wrapper) { transition: none; } }
</style>
