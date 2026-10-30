// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  loggedIn: false,
  restored: false,
  expired: false,
  guard: null as null | ((to: { name: string; fullPath: string; meta: Record<string, string>; query: Record<string, string> }) => Promise<unknown>),
}))

vi.mock('vue-router', () => ({
  createWebHistory: () => ({}),
  createRouter: () => ({ beforeEach: (guard: typeof state.guard) => { state.guard = guard }, afterEach: () => {}, onError: () => {} }),
}))
vi.mock('@/layouts/BasicLayout.vue', () => ({ default: {} }))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({
  isLoggedIn: () => state.loggedIn,
  userInfo: { permissions: [] },
  hasPerm: () => true,
  hasAnyPerm: () => true,
}) }))
vi.mock('@/api/token', () => ({ restoreTokensFromPeer: async () => {
  if (state.restored) state.loggedIn = true
  return state.restored
}, expireIfNeeded: () => {
  if (state.expired) state.loggedIn = false
  return state.expired
} }))

beforeEach(async () => {
  state.loggedIn = false
  state.restored = false
  state.expired = false
  await import('./index')
})

const target = { name: 'cmdb-apps', fullPath: '/cmdb/apps?id=7', meta: {}, query: {} }

it('新窗口取得已有会话后继续访问原详情页', async () => {
  state.restored = true
  expect(await state.guard?.(target)).toBe(true)
})

it('没有其他登录窗口时回登录页并保留目标地址', async () => {
  expect(await state.guard?.(target)).toEqual({ name: 'login', query: { redirect: '/cmdb/apps?id=7' } })
})

it('已有登录令牌到达固定会话截止时拒绝进入受保护页面', async () => {
  state.loggedIn = true
  state.expired = true
  expect(await state.guard?.(target)).toEqual({ name: 'login', query: { redirect: '/cmdb/apps?id=7' } })
})
