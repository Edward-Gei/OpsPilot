// 令牌存储：独立小模块避免 http <-> store 循环依赖
// sessionStorage 持久化（关浏览器即失效，refresh 7 天有效期仅在会话内使用）
const ACCESS_KEY = 'opspilot_access_token'
const REFRESH_KEY = 'opspilot_refresh_token'

export function getAccessToken(): string {
  return sessionStorage.getItem(ACCESS_KEY) || ''
}

export function getRefreshToken(): string {
  return sessionStorage.getItem(REFRESH_KEY) || ''
}

/** 保存令牌对（登录成功 / 刷新旋转后调用） */
export function setTokens(access: string, refresh: string): void {
  sessionStorage.setItem(ACCESS_KEY, access)
  sessionStorage.setItem(REFRESH_KEY, refresh)
}

/** 清空令牌（登出 / 会话失效） */
export function clearTokens(): void {
  sessionStorage.removeItem(ACCESS_KEY)
  sessionStorage.removeItem(REFRESH_KEY)
}
