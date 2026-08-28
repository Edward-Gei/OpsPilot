// 路由：/login 独立页，其余挂 BasicLayout；守卫做登录校验与权限过滤
import { createRouter, createWebHistory } from 'vue-router'

import BasicLayout from '@/layouts/BasicLayout.vue'
import { useUserStore } from '@/stores/user'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/Login.vue'),
      meta: { title: '登录' },
    },
    {
      path: '/forgot-password',
      name: 'forgot-password',
      component: () => import('@/views/ForgotPassword.vue'),
      meta: { title: '找回密码' },
    },
    {
      path: '/reset-password',
      name: 'reset-password',
      component: () => import('@/views/ResetPassword.vue'),
      meta: { title: '重置密码' },
    },
    {
      path: '/',
      component: BasicLayout,
      children: [
        {
          path: '',
          name: 'dashboard',
          component: () => import('@/views/Dashboard.vue'),
          meta: { title: '工作台' },
        },
        {
          path: 'profile',
          name: 'profile',
          component: () => import('@/views/Profile.vue'),
          meta: { title: '个人中心' },
        },
        {
          path: 'profile/tokens',
          name: 'profile-tokens',
          component: () => import('@/views/ApiTokens.vue'),
          meta: { title: '访问密钥' },
        },
        {
          path: 'cmdb/hosts',
          name: 'cmdb-hosts',
          component: () => import('@/views/cmdb/HostList.vue'),
          meta: { title: '主机管理', perm: 'cmdb:read' },
        },
        {
          path: 'cmdb/apps',
          name: 'cmdb-apps',
          component: () => import('@/views/cmdb/AppList.vue'),
          meta: { title: '应用管理', perm: 'cmdb:read' },
        },
        {
          path: 'job/templates',
          name: 'job-templates',
          redirect: '/job/templates/tickets',
          meta: { title: '模板管理', perm: 'template:read' },
          children: [
            {
              path: 'tickets',
              name: 'job-template-tickets',
              component: () => import('@/views/job/TicketTemplateList.vue'),
              meta: { title: '工单模板', perm: 'template:read', menuKey: 'job-template-tickets' },
            },
            {
              path: 'processes',
              name: 'job-template-processes',
              component: () => import('@/views/job/ProcessTemplateList.vue'),
              meta: { title: '流程模板', perm: 'template:read', menuKey: 'job-template-processes' },
            },
          ],
        },
        {
          path: 'job/credentials',
          name: 'job-credentials',
          component: () => import('@/views/job/CredentialList.vue'),
          meta: { title: '凭据管理', perms: ['credential:read', 'secret:read'] },
        },
        {
          path: 'ticket/list',
          name: 'ticket-list',
          component: () => import('@/views/ticket/TicketList.vue'),
          meta: { title: '工单中心', perm: 'ticket:read' },
        },
        {
          path: 'ticket/todo',
          name: 'ticket-todo',
          component: () => import('@/views/ticket/TodoList.vue'),
          meta: { title: '待办审批', perm: 'ticket:approve' },
        },
        {
          path: 'executions',
          name: 'execution-list',
          component: () => import('@/views/execution/ExecutionList.vue'),
          meta: { title: '执行中心', perm: 'execution:read' },
        },
        {
          path: 'executions/:id',
          name: 'execution-detail',
          component: () => import('@/views/execution/ExecutionDetail.vue'),
          // menuKey：详情页侧边栏仍高亮「执行中心」
          meta: { title: '执行详情', perm: 'execution:read', menuKey: 'execution-list' },
        },
        {
          path: 'notify',
          name: 'notify',
          component: () => import('@/views/notify/NotifyCenter.vue'),
          meta: { title: '通知中心', perm: 'notify:read' },
        },
        {
          path: 'audit',
          name: 'audit-logs',
          component: () => import('@/views/audit/AuditLogList.vue'),
          meta: { title: '安全审计', perm: 'audit:read' },
        },
        {
          path: 'system/users',
          name: 'system-users',
          component: () => import('@/views/system/UserList.vue'),
          meta: { title: '用户管理', perm: 'user:read' },
        },
        {
          path: 'system/roles',
          name: 'system-roles',
          component: () => import('@/views/system/RoleList.vue'),
          meta: { title: '角色管理', perm: 'role:read' },
        },
        {
          path: 'system/settings',
          name: 'system-settings',
          component: () => import('@/views/system/SettingsPage.vue'),
          meta: { title: '系统设置', perm: 'system:config' },
        },
      ],
    },
  ],
})

// 登录守卫：未登录回登录页；已登录补拉用户信息；meta.perm 权限过滤
router.beforeEach(async (to) => {
  const userStore = useUserStore()
  if (to.name === 'login' || to.name === 'forgot-password' || to.name === 'reset-password') {
    // 已登录访问登录页直接回工作台（携带 SSO ticket 时放行走换票流程）
    if (userStore.isLoggedIn() && !to.query.ticket) return { path: '/' }
    return true
  }
  if (!userStore.isLoggedIn()) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  // 刷新页面后用户信息丢失：先补拉（失败由拦截器清会话回登录页）
  if (!userStore.userInfo) {
    try {
      await userStore.loadUserInfo()
    } catch {
      return { name: 'login' }
    }
  }
  const perm = to.meta.perm as string | undefined
  const perms = to.meta.perms as string[] | undefined
  if ((perm && !userStore.hasPerm(perm)) || (perms && !userStore.hasAnyPerm(perms))) {
    return { path: '/' }
  }
  return true
})

// 统一页面标题
router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title as string} - OpsPilot` : 'OpsPilot'
})

export default router
