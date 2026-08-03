<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { ApiError } from '@/api/http'
import * as authApi from '@/api/auth'

const route = useRoute()
const router = useRouter()
const token = String(route.query.token || '')
const status = ref<'loading' | 'valid' | 'expired' | 'used' | 'invalid'>('loading')
const loading = ref(false)
const policy = ref<{ min_length: number; require_complex: boolean } | null>(null)
const form = reactive({ newPassword: '', confirm: '' })

onMounted(async () => {
  if (!token) { status.value = 'invalid'; return }
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
  if (form.newPassword !== form.confirm) { message.warning('两次输入的新密码不一致'); return }
  loading.value = true
  try {
    await authApi.resetPassword(token, form.newPassword)
    message.success('密码重置成功，请使用新密码登录。')
    await router.push('/login')
  } catch (error) {
    if (error instanceof ApiError && [40001, 42201].includes(error.code)) message.error(error.message)
  } finally { loading.value = false }
}
</script>

<template>
  <div class="public-page">
    <div class="public-card">
      <template v-if="status === 'loading'"><a-spin /></template>
      <template v-else-if="status === 'valid'">
        <h1>重置密码</h1>
        <p v-if="policy" class="policy">密码至少 {{ policy.min_length }} 位{{ policy.require_complex ? '，且包含字母和数字' : '' }}。</p>
        <a-form layout="vertical" @finish="submit">
          <a-form-item label="新密码"><a-input-password v-model:value="form.newPassword" size="large" /></a-form-item>
          <a-form-item label="确认新密码"><a-input-password v-model:value="form.confirm" size="large" /></a-form-item>
          <a-button type="primary" html-type="submit" block size="large" :loading="loading">确认重置密码</a-button>
        </a-form>
      </template>
      <template v-else-if="status === 'expired'"><h1>链接已过期</h1><p>该重置链接已过期，请重新申请。</p><a-button type="primary" block @click="router.push('/forgot-password')">重新申请</a-button></template>
      <template v-else-if="status === 'used'"><h1>链接已使用</h1><p>该重置链接已使用，请返回登录。</p><a-button type="primary" block @click="router.push('/login')">返回登录</a-button></template>
      <template v-else><h1>链接无效</h1><p>该重置链接无效，请重新申请。</p><a-button type="primary" block @click="router.push('/forgot-password')">重新申请</a-button></template>
    </div>
  </div>
</template>

<style scoped>
.public-page { min-height: 100vh; display: flex; align-items: center; justify-content: center; background: var(--bg-layout); padding: 24px; }
.public-card { width: min(400px, 100%); padding: 40px; background: var(--bg-card); border: 1px solid var(--border); border-radius: 16px; box-shadow: var(--shadow-card-hover); }
h1 { margin: 0 0 12px; font-size: 24px; color: var(--text-1); }
.policy, p { color: var(--text-3); line-height: 1.7; }
</style>
