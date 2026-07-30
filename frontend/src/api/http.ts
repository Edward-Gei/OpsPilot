// Axios 封装：统一解包 {code, message, data}；注入 Authorization；
// 40102 静默刷新重试（单飞）；40103/40104/40105 三态静默透传给登录页处理
import axios, { AxiosError, type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'
import { message } from 'ant-design-vue'
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from './token'

export interface ApiResponse<T = unknown> {
  code: number
  message: string
  data: T
}

/** 业务错误：携带后端业务码与 data 载荷（三态流程用） */
export class ApiError extends Error {
  code: number
  data: unknown
  constructor(code: number, msg: string, data?: unknown) {
    super(msg)
    this.code = code
    this.data = data
  }
}

// 认证流程业务码：不弹全局错误提示，由登录页状态机接管
const AUTH_FLOW_CODES = new Set([40103, 40104, 40105])
// 这些接口的 40101 属于正常业务反馈（密码错/验证码错），不触发会话清理跳转
const NO_REDIRECT_URLS = ['/auth/login', '/auth/refresh', '/auth/mfa/', '/auth/sso/', '/auth/password']

const http = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

// 请求拦截：注入 Bearer 头
http.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 刷新单飞：并发 401 只发起一次 refresh，其余请求等待同一 Promise
let refreshing: Promise<boolean> | null = null

/** 用 refresh_token 换新令牌对（旋转），失败返回 false */
async function tryRefresh(): Promise<boolean> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) return false
  try {
    const resp = await axios.post<ApiResponse<{ access_token: string; refresh_token: string }>>(
      '/api/v1/auth/refresh',
      { refresh_token: refreshToken },
    )
    if (resp.data.code === 0) {
      setTokens(resp.data.data.access_token, resp.data.data.refresh_token)
      return true
    }
  } catch {
    /* 刷新失败走会话清理 */
  }
  return false
}

/** 主动刷新令牌对（WS 被 4401 拒后重连前调用）：复用 40102 的单飞 Promise */
export function refreshTokenPair(): Promise<boolean> {
  refreshing = refreshing ?? tryRefresh().finally(() => (refreshing = null))
  return refreshing
}

/** 会话失效：清令牌并回登录页 */
function forceLogin(): void {
  clearTokens()
  if (!window.location.pathname.startsWith('/login')) {
    window.location.href = '/login'
  }
}

// 响应拦截：解包 + 三态透传 + 40102 静默续期
http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiResponse>) => {
    const body = error.response?.data
    const config = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined
    if (!body || typeof body.code !== 'number') {
      message.error(error.message || '网络异常，请稍后重试')
      return Promise.reject(error)
    }
    // Access Token 过期：静默刷新后重放原请求（仅一次）
    if (body.code === 40102 && config && !config._retried && !config.url?.includes('/auth/refresh')) {
      refreshing = refreshing ?? tryRefresh().finally(() => (refreshing = null))
      if (await refreshing) {
        config._retried = true
        config.headers.Authorization = `Bearer ${getAccessToken()}`
        return http.request(config)
      }
      forceLogin()
      return Promise.reject(new ApiError(body.code, '登录已过期，请重新登录'))
    }
    // 登录三态：静默交给调用方（登录页状态机）
    if (!AUTH_FLOW_CODES.has(body.code)) {
      message.error(body.message || '请求失败')
      // 未认证且非登录类接口：会话已失效，回登录页
      const url = config?.url || ''
      if (
        (body.code === 40101 || body.code === 40102) &&
        !NO_REDIRECT_URLS.some((u) => url.includes(u))
      ) {
        forceLogin()
      }
    }
    return Promise.reject(new ApiError(body.code, body.message, body.data))
  },
)

/** 通用请求：直接返回业务 data，调用方无需再解包 */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  const response = await http.request<ApiResponse<T>>(config)
  return response.data.data
}

export default http
