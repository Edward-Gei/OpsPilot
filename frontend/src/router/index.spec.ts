// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest'
import type { RouteRecordRaw } from 'vue-router'

const state = vi.hoisted(() => ({
  loggedIn: false,
  restored: false,
  expired: false,
  routes: [] as RouteRecordRaw[],
  permissions: null as Set<string> | null,
  guard: null as null | ((to: { name: string; fullPath: string; meta: Record<string, unknown>; query: Record<string, string> }) => Promise<unknown>),
}))

vi.mock('vue-router', () => ({
  createWebHistory: () => ({}),
  createRouter: ({ routes }: { routes: RouteRecordRaw[] }) => {
    state.routes = routes
    return { beforeEach: (guard: typeof state.guard) => { state.guard = guard }, afterEach: () => {}, onError: () => {} }
  },
}))
vi.mock('@/layouts/BasicLayout.vue', () => ({ default: {} }))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({
  isLoggedIn: () => state.loggedIn,
  userInfo: { permissions: [] },
  hasPerm: (permission: string) => state.permissions === null || state.permissions.has(permission),
  hasAnyPerm: (permissions: string[]) => state.permissions === null || permissions.some(permission => state.permissions!.has(permission)),
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
  state.permissions = null
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

it('数据库旧详情编辑和新建地址统一由台账承载，保留原权限', async () => {
  const routes = state.routes.flatMap(route => route.children || [])
  const list = routes.find(route => route.name === 'database-instances')!
  const loadList = list.component as () => Promise<{ default: unknown }>
  const component = (await loadList()).default
  for (const name of ['database-instance-detail', 'database-instance-edit', 'database-instance-new']) {
    const route = routes.find(item => item.name === name)!
    expect((await (route.component as typeof loadList)()).default).toBe(component)
  }
  expect(routes.find(route => route.name === 'database-instance-detail')?.meta?.perms).toContain('database:query')
  expect(routes.find(route => route.name === 'database-instance-edit')?.meta?.perms).toEqual(['database:write', 'database:grant'])
  expect(routes.find(route => route.name === 'database-workbench')?.meta?.title).toBe('SQL工作台')
})

it.each([
  ['database:grant', true, false],
  ['database:write', true, true],
  ['database:read', false, false],
])('旧编辑与新建地址遵循%s的独立准入范围', async (permission, edit, create) => {
  state.loggedIn = true
  state.permissions = new Set([permission as string])
  const routes = state.routes.flatMap(route => route.children || [])
  for (const [name, allowed] of [['database-instance-edit', edit], ['database-instance-new', create]] as const) {
    const route = routes.find(item => item.name === name)!
    expect(await state.guard?.({ name, fullPath: route.path, meta: route.meta || {}, query: {} })).toEqual(allowed ? true : { path: '/' })
  }
})
