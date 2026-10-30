// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, type RouteComponent } from 'vue-router'
import { message } from 'ant-design-vue'
import { ApiError } from '@/api/http'
import { handleRouteError } from '@/router/routeLoadRecovery'
import Login from './Login.vue'

const api = vi.hoisted(() => ({
  login: vi.fn(), fetchMe: vi.fn(), fetchSsoOptions: vi.fn(), logout: vi.fn(),
  mfaVerify: vi.fn(), mfaSetup: vi.fn(), mfaBind: vi.fn(), changePassword: vi.fn(), ssoExchange: vi.fn(),
}))
vi.mock('@/api/auth', () => api)
vi.mock('ant-design-vue', () => ({ message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }))
vi.mock('qrcode', () => ({ default: { toDataURL: async () => 'data:image/png;base64,test' } }))

const input = {
  props: ['value', 'size'], emits: ['update:value'],
  template: '<input :value="value" @input="$emit(\'update:value\', $event.target.value)" />',
}
let wrapper: ReturnType<typeof shallowMount> | undefined
let entry: HTMLScriptElement | undefined

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  api.login.mockResolvedValue({ access_token: 'access', refresh_token: 'refresh', token_type: 'bearer', expires_in: 900 })
  api.fetchMe.mockResolvedValue({ id: 1, username: 'alice', display_name: 'Alice', email: null,
    source: 'local', mfa_enabled: false, last_login_at: null, roles: [], permissions: [] })
  api.fetchSsoOptions.mockResolvedValue({ oidc_enabled: false, sso_enabled: false, forgot_password_enabled: false })
  api.mfaVerify.mockResolvedValue({ access_token: 'access', refresh_token: 'refresh', token_type: 'bearer', expires_in: 900 })
  api.mfaBind.mockResolvedValue({ access_token: 'access', refresh_token: 'refresh', token_type: 'bearer', expires_in: 900 })
  api.changePassword.mockResolvedValue({ access_token: 'access', refresh_token: 'refresh', token_type: 'bearer', expires_in: 900 })
  api.mfaSetup.mockResolvedValue({ secret: 'test-secret', otpauth_uri: 'otpauth://totp/test' })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  sessionStorage.clear()
  entry?.remove()
  entry = undefined
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function openLogin(component: () => Promise<RouteComponent>) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/login', name: 'login', component: { template: '<div />' } },
    { path: '/', name: 'dashboard', component },
  ] })
  router.onError((error, to) => { void handleRouteError(error, to.fullPath) })
  await router.push('/login')
  wrapper = shallowMount(Login, { global: {
    plugins: [createPinia(), router], renderStubDefaultSlot: true,
    stubs: {
      AForm: { template: '<form @submit.prevent="$emit(\'finish\')"><slot /></form>' },
      AFormItem: { template: '<label><slot /></label>' },
      AInput: input, AInputPassword: input, ASpin: true,
      AButton: { props: ['loading'], template: '<button :disabled="loading"><slot /></button>' },
    },
  } })
  await flushPromises()
  return { wrapper, router }
}

async function submitPassword(current: NonNullable<typeof wrapper>) {
  const inputs = current.findAll('input')
  await inputs[0]!.setValue('alice')
  await inputs[1]!.setValue('password-123')
  await current.find('form').trigger('submit')
  await flushPromises()
}

it('目标页面加载完成前保持登录处理中，导航完成后才提示成功', async () => {
  const page = deferred<RouteComponent>()
  const { wrapper, router } = await openLogin(() => page.promise)
  await submitPassword(wrapper)
  const pending = { success: vi.mocked(message.success).mock.calls.length,
    disabled: wrapper.find('button').attributes('disabled'), path: router.currentRoute.value.path }
  page.resolve({ template: '<div>工作台</div>' })
  await flushPromises()

  expect(pending.path).toBe('/login')
  expect(pending.success).toBe(0)
  expect(pending.disabled).toBeDefined()
  expect(router.currentRoute.value.path).toBe('/')
  expect(message.success).toHaveBeenCalledWith('登录成功')
})

it('目标页面懒加载失败时停留登录页并反馈错误，不显示登录成功', async () => {
  const page = deferred<RouteComponent>()
  const { wrapper, router } = await openLogin(() => page.promise)
  const replace = vi.spyOn(router, 'replace')
  const push = vi.spyOn(router, 'push')
  await submitPassword(wrapper)
  // 观察真实导航 Promise，避免旧实现未接管拒绝时中断本条回归。
  void ((replace.mock.results[0]?.value ?? push.mock.results[0]!.value) as ReturnType<typeof router.push>).catch(() => {})
  page.reject(new Error('Failed to fetch dynamically imported module: /assets/Dashboard-old.js'))
  await flushPromises()

  expect(router.currentRoute.value.path).toBe('/login')
  expect(sessionStorage.getItem('opspilot_access_token')).toBe('access')
  expect(message.success).not.toHaveBeenCalled()
  expect(message.error).toHaveBeenCalled()
  expect(wrapper.find('button').attributes('disabled')).toBeUndefined()
})

it.each([
  [40103, '安全验证', 'mfaVerify'],
  [40104, '绑定 MFA 认证器', 'mfaBind'],
] as const)('MFA 挑战 %s 不提前授予会话，最后一步验证后共用导航收尾', async (code, title, action) => {
  api.login.mockRejectedValueOnce(new ApiError(code, '需要 MFA', { mfa_token: 'test-mfa-token' }))
  const { wrapper, router } = await openLogin(async () => ({ template: '<div>工作台</div>' }))
  await submitPassword(wrapper)
  expect(wrapper.text()).toContain(title)
  expect(sessionStorage.getItem('opspilot_access_token')).toBeNull()
  expect(message.success).not.toHaveBeenCalled()
  expect(router.currentRoute.value.path).toBe('/login')

  await wrapper.find('input').setValue('123456')
  await wrapper.find('form').trigger('submit')
  await flushPromises()
  expect(api[action]).toHaveBeenCalledWith(...(action === 'mfaVerify' ? ['test-mfa-token', '123456'] : ['123456', 'test-mfa-token']))
  expect(router.currentRoute.value.path).toBe('/')
  expect(message.success).toHaveBeenCalledWith('登录成功')
})

it('强制改密完成前不授予会话，完成后进入工作台', async () => {
  api.login.mockRejectedValueOnce(new ApiError(40105, '需要修改密码', { change_token: 'test-change-token' }))
  const { wrapper, router } = await openLogin(async () => ({ template: '<div>工作台</div>' }))
  await submitPassword(wrapper)
  expect(wrapper.text()).toContain('修改初始密码')
  expect(sessionStorage.getItem('opspilot_access_token')).toBeNull()
  expect(message.success).not.toHaveBeenCalled()
  const inputs = wrapper.findAll('input')
  await inputs[1]!.setValue('new-password-123')
  await inputs[2]!.setValue('new-password-123')
  await wrapper.find('form').trigger('submit')
  await flushPromises()
  expect(api.changePassword).toHaveBeenCalledWith('password-123', 'new-password-123', 'test-change-token')
  expect(router.currentRoute.value.path).toBe('/')
  expect(message.success).toHaveBeenCalledWith('登录成功')
})

it('认证动作失败时提示错误并解除处理中，不写入会话或跳转', async () => {
  api.login.mockRejectedValueOnce(new Error('认证请求失败'))
  const { wrapper, router } = await openLogin(async () => ({ template: '<div>工作台</div>' }))
  await submitPassword(wrapper)
  expect(router.currentRoute.value.path).toBe('/login')
  expect(sessionStorage.getItem('opspilot_access_token')).toBeNull()
  expect(message.success).not.toHaveBeenCalled()
  expect(message.error).toHaveBeenCalledWith('认证请求失败')
  expect(wrapper.find('button').attributes('disabled')).toBeUndefined()
})

it('导航被守卫取消时反馈失败，不提前显示登录成功', async () => {
  const { wrapper, router } = await openLogin(async () => ({ template: '<div>工作台</div>' }))
  router.beforeEach(to => to.name !== 'dashboard')
  await submitPassword(wrapper)

  expect(router.currentRoute.value.path).toBe('/login')
  expect(message.success).not.toHaveBeenCalled()
  expect(message.error).toHaveBeenCalled()
  expect(wrapper.find('button').attributes('disabled')).toBeUndefined()
})

it('在同一登录页再次发起认证后，前次导航的迟到版本检查静默结束', async () => {
  entry = document.createElement('script')
  entry.type = 'module'
  entry.src = '/assets/index-old.js'
  document.head.append(entry)
  const version = deferred<{ ok: boolean; text: () => Promise<string> }>()
  const fetchIndex = vi.fn().mockReturnValueOnce(version.promise)
  vi.stubGlobal('fetch', fetchIndex)
  const page = deferred<RouteComponent>()
  const { wrapper, router } = await openLogin(() => page.promise)
  await submitPassword(wrapper)
  page.reject(new TypeError('Failed to fetch dynamically imported module: /assets/Dashboard-old.js'))
  await flushPromises()
  expect(fetchIndex).toHaveBeenCalledOnce()

  const authentication = deferred<never>()
  api.login.mockReturnValueOnce(authentication.promise)
  await submitPassword(wrapper)
  expect(router.currentRoute.value.path).toBe('/login')
  expect(wrapper.find('button').attributes('disabled')).toBeDefined()
  version.resolve({ ok: true, text: async () => '<script type="module" src="/assets/index-old.js"></script>' })
  await flushPromises()
  const staleErrors = vi.mocked(message.error).mock.calls.length
  authentication.reject(new Error('第二次认证失败'))
  await flushPromises()
  expect(staleErrors).toBe(0)
  expect(message.error).toHaveBeenCalledTimes(1)
  expect(message.error).toHaveBeenCalledWith('第二次认证失败')
})
