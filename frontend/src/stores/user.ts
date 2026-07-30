// 用户状态：令牌对 + 用户信息 + 权限判定（路由守卫与菜单显隐依据）
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { fetchMe, logout as apiLogout, type MeInfo, type TokenPair } from '@/api/auth'
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from '@/api/token'

export const useUserStore = defineStore('user', () => {
  const userInfo = ref<MeInfo | null>(null)

  const permissions = computed(() => new Set(userInfo.value?.permissions ?? []))

  /** 是否已登录（存在 access token 即视为已登录，有效性由拦截器兜底） */
  function isLoggedIn(): boolean {
    return Boolean(getAccessToken())
  }

  /** 权限判定：菜单/按钮显隐与路由守卫共用 */
  function hasPerm(perm: string): boolean {
    return permissions.value.has(perm)
  }

  /** 登录成功后保存令牌对 */
  function applyTokens(tokens: TokenPair): void {
    setTokens(tokens.access_token, tokens.refresh_token)
  }

  /** 拉取当前用户信息（守卫在信息缺失时调用） */
  async function loadUserInfo(): Promise<MeInfo> {
    userInfo.value = await fetchMe()
    return userInfo.value
  }

  /** 登出：吊销 refresh + 清空本地状态 */
  async function logout(): Promise<void> {
    const refresh = getRefreshToken()
    try {
      if (refresh) await apiLogout(refresh)
    } catch {
      /* 令牌已失效也继续清理本地 */
    }
    reset()
  }

  /** 清空会话状态 */
  function reset(): void {
    clearTokens()
    userInfo.value = null
  }

  return { userInfo, isLoggedIn, hasPerm, applyTokens, loadUserInfo, logout, reset }
})
