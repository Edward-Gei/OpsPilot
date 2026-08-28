// 认证 API（04-API设计 §2）：登录三态 / MFA / 令牌 / 改密 / SSO
import { request } from './http'

/** 令牌对（登录成功 / 刷新 / MFA 通过 / 改密后返回） */
export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

/** 当前用户信息（/auth/me） */
export interface MeInfo {
  id: number
  username: string
  display_name: string
  email: string | null
  source: string
  mfa_enabled: boolean
  last_login_at: string | null
  roles: { id: number; code: string; name: string }[]
  permissions: string[]
}

/** 账号密码登录：三态（40103/40104/40105）以 ApiError 形式抛出 */
export function login(username: string, password: string) {
  return request<TokenPair>({ url: '/auth/login', method: 'post', data: { username, password } })
}

export function logout(refreshToken: string) {
  return request<null>({ url: '/auth/logout', method: 'post', data: { refresh_token: refreshToken } })
}

export function fetchMe() {
  return request<MeInfo>({ url: '/auth/me', method: 'get' })
}

/** 个人资料自助修改（仅显示名/邮箱） */
export function updateProfile(displayName: string, email: string | null) {
  return request<null>({
    url: '/auth/me',
    method: 'put',
    data: { display_name: displayName, email },
  })
}

/** 获取 MFA 绑定二维码（mfa_token 走登录流程；不传则用 access token 自助绑定） */
export function mfaSetup(mfaToken?: string) {
  return request<{ secret: string; otpauth_uri: string }>({
    url: '/auth/mfa/setup',
    method: 'post',
    data: { mfa_token: mfaToken },
  })
}

/** 确认绑定：登录流程返回令牌对；个人中心自助绑定返回 null */
export function mfaBind(code: string, mfaToken?: string) {
  return request<TokenPair | null>({
    url: '/auth/mfa/bind',
    method: 'post',
    data: { mfa_token: mfaToken, code },
  })
}

/** 登录第二步动态码验证（40105 可能继续抛出） */
export function mfaVerify(mfaToken: string, code: string) {
  return request<TokenPair>({
    url: '/auth/mfa/verify',
    method: 'post',
    data: { mfa_token: mfaToken, code },
  })
}

/** 修改密码：changeToken 通道（强制改密）成功返回令牌对；个人中心通道返回 null */
export function changePassword(oldPassword: string, newPassword: string, changeToken?: string) {
  return request<TokenPair | null>({
    url: '/auth/password',
    method: 'put',
    data: { old_password: oldPassword, new_password: newPassword, change_token: changeToken },
  })
}

/** SSO 回调 ticket 换正式令牌（三态同样可能抛出） */
export function ssoExchange(ticket: string) {
  return request<TokenPair>({ url: '/auth/sso/exchange', method: 'post', data: { ticket } })
}

/** 登录页 SSO 选项（OIDC 未配置则隐藏按钮） */
export function fetchSsoOptions() {
  return request<{ oidc_enabled: boolean; sso_enabled: boolean; forgot_password_enabled: boolean }>({ url: '/auth/sso/options', method: 'get' })
}

export function fetchPasswordPolicy() {
  return request<{ min_length: number; require_complex: boolean }>({ url: '/auth/password-policy', method: 'get' })
}

export function forgotPassword(email: string) {
  return request<{ message: string }>({ url: '/auth/forgot-password', method: 'post', data: { email } })
}

export function validateResetToken(token: string) {
  return request<{ status: 'valid' | 'expired' | 'used' | 'invalid'; expires_in_seconds?: number }>({
    url: '/auth/reset-password/validate', method: 'get', params: { token },
  })
}

export function resetPassword(token: string, newPassword: string) {
  return request<null>({ url: '/auth/reset-password', method: 'post', data: { token, new_password: newPassword } })
}
