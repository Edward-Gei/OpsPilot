# RBAC Permission Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已确认的适度拆分 RBAC 权限矩阵落到后端鉴权、内置角色种子、角色管理 API、通知 API、前端权限控制和回归测试中。

**Architecture:** 继续使用现有 `Permission`、`RolePermission` 和 `require_perm` 机制，不新增权限继承关系；每个权限点独立授权。开发阶段清理并重建权限数据，种子以新的 `PERMISSIONS` 与 `BUILTIN_ROLES` 为唯一来源；角色服务只保护 `admin` 角色不可编辑/删除，其他内置角色允许编辑但不可删除。

**Tech Stack:** FastAPI, SQLAlchemy async, pytest, Vue 3, TypeScript, Pinia, Ant Design Vue.

---

### Task 1: 更新权限常量和内置角色矩阵

**Files:**
- Modify: `backend/app/core/constants.py`
- Test: `backend/tests/test_smoke.py`

- [ ] **Step 1: 写权限集合回归测试**

在 `test_smoke.py` 增加断言，确认 `PERMISSIONS` 包含新增权限且不包含 `notify:config`；确认 `admin` 拥有全部权限，`ops` 拥有 `cmdb:import`，非 admin 内置角色拥有按方案确定的权限，且 `execution:force_control` 和 `notify:test` 默认只在 admin 中。

- [ ] **Step 2: 运行失败测试**

Run: `python -m pytest tests/test_smoke.py -q`

Expected: FAIL，因为当前常量仍包含 `notify:config`，且缺少拆分权限。

- [ ] **Step 3: 实现最小常量变更**

将 `PERMISSIONS` 更新为约 30 个独立权限点：增加 `cmdb:delete/import`、`credential:delete`、`job_host:delete`、`template:delete`、`execution:force_control`、`notify:read/write/test`，删除 `notify:config`；不增加 `ticket:withdraw`。按已确认矩阵更新 `BUILTIN_ROLES`，`admin` 使用全量权限，`ops` 保留 `cmdb:import`。

- [ ] **Step 4: 运行通过测试**

Run: `python -m pytest tests/test_smoke.py -q`

Expected: PASS。

### Task 2: 调整角色服务的 admin 保护和内置角色删除规则

**Files:**
- Modify: `backend/app/services/rbac_service.py`
- Modify: `backend/app/api/v1/roles.py`
- Modify: `backend/tests/test_rbac_api.py`

- [ ] **Step 1: 写角色行为测试**

覆盖以下行为：admin 更新自身返回业务拒绝；admin 删除返回业务拒绝；其他内置角色可以更新名称、描述和 `permissions`；其他内置角色删除仍被拒绝；自定义角色仍可编辑和删除；角色更新后用户权限缓存失效。

- [ ] **Step 2: 运行失败测试**

Run: `python -m pytest tests/test_rbac_api.py -q`

Expected: FAIL，因为当前所有内置角色都禁止修改权限，admin 也没有按角色编码单独保护。

- [ ] **Step 3: 实现角色服务规则**

让 `update_role` 接收并使用 `role.code` 判断：仅 `admin` 拒绝任何修改；其他内置角色允许更新名称、描述和权限矩阵。让 `delete_role` 保持所有内置角色不可删除，并用明确的 `admin` 保护测试验证。

- [ ] **Step 4: 保持路由层边界**

继续由 `role:write` 控制角色创建、更新、删除；不改变角色列表和权限列表读取权限。清理路由及前端中“所有内置角色权限锁定”的过期语义。

- [ ] **Step 5: 运行角色测试**

Run: `python -m pytest tests/test_rbac_api.py -q`

Expected: PASS。

### Task 3: 将后端高风险接口切换到独立权限

**Files:**
- Modify: `backend/app/api/v1/cmdb.py`
- Modify: `backend/app/api/v1/credentials.py`
- Modify: `backend/app/api/v1/job_hosts.py`
- Modify: `backend/app/api/v1/templates.py`
- Modify: `backend/app/api/v1/process_templates.py`
- Modify: `backend/app/api/v1/tickets.py`
- Modify: `backend/app/api/v1/notify.py`
- Modify: `backend/app/api/v1/audit.py`
- Modify: `backend/app/api/v1/executions.py` only if force-control route exists there; otherwise keep execution query routes unchanged
- Test: `backend/tests/test_cmdb_api.py`, `backend/tests/test_rbac_api.py`, `backend/tests/test_notify_api.py`, relevant existing credential/job-host/template/ticket tests

- [ ] **Step 1: 写接口权限回归测试**

为各拆分接口建立最小拒绝/允许断言：无 `cmdb:delete` 不能删除、无 `cmdb:import` 不能下载导入模板或导入、无资源 `delete` 权限不能删除、无 `template:delete` 不能删除模板、无 `execution:force_control` 不能强制中止、通知读写测试分别控制对应端点、审计导出继续只认 `audit:export`。

- [ ] **Step 2: 运行相关测试确认失败**

Run: `python -m pytest tests/test_cmdb_api.py tests/test_notify_api.py tests/test_rbac_api.py -q`

Expected: FAIL，原因是现有端点仍使用通用 `write` 或 `notify:config`。

- [ ] **Step 3: 替换端点鉴权声明**

按操作替换 `require_perm`：删除端点使用各自 `*:delete`；CMDB 导入模板下载和导入使用 `cmdb:import`；通知读取端点使用 `notify:read`，配置与映射更新使用 `notify:write`，测试端点使用 `notify:test`；执行控制中的 `force-abort` 使用 `execution:force_control`，其他控制继续使用 `execution:control`。

- [ ] **Step 4: 保持工单撤回业务规则**

不新增 `ticket:withdraw`；工单审批中撤回端点继续使用现有业务条件，不增加新的权限依赖。工单创建/提交和审批权限保持 `ticket:write`、`ticket:approve`。

- [ ] **Step 5: 运行后端专项测试**

Run: `python -m pytest tests/test_cmdb_api.py tests/test_notify_api.py tests/test_rbac_api.py tests/test_ticket_api.py -q`

Expected: PASS。

### Task 4: 更新前端权限判断，不改变既有 UI 样式

**Files:**
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/layouts/BasicLayout.vue`
- Modify: `frontend/src/views/Dashboard.vue`
- Modify: `frontend/src/views/notify/NotifyCenter.vue`
- Modify: `frontend/src/views/system/RoleList.vue`
- Modify: `frontend/src/views/cmdb/HostList.vue`
- Modify: `frontend/src/views/cmdb/AppList.vue`
- Modify: `frontend/src/views/job/CredentialList.vue`
- Modify: `frontend/src/views/system/JobHostPanel.vue`
- Modify: `frontend/src/views/job/TicketTemplateList.vue`
- Modify: `frontend/src/views/job/ProcessTemplateList.vue`
- Modify: `frontend/src/views/execution/ExecutionDetail.vue`
- Modify: `frontend/src/api/notify.ts`

- [ ] **Step 1: 增加前端权限显示测试或类型检查基线**

确认所有 `notify:config` 引用清零；确认删除、导入、强制中止按钮分别使用新权限；确认角色页仅禁用 admin 编辑，其他内置角色的权限复选框可编辑。项目无现成 Vue 单测时，以 `rg` 检查和 `npm.cmd run type-check` 作为验证。

- [ ] **Step 2: 更新路由、菜单和快捷入口权限**

将通知中心入口从 `notify:config` 改为 `notify:read`；保持其他页面入口使用各模块 `read` 权限，不改变菜单布局和样式。

- [ ] **Step 3: 更新操作按钮权限**

在现有页面中拆分 `canWrite` 为必要的 `canWrite`、`canDelete`、`canImport` 或等价计算值，仅控制按钮显示/禁用，不改现有结构和 CSS。执行详情单独用 `execution:force_control` 控制强制中止按钮。

- [ ] **Step 4: 更新通知中心权限**

通知中心以 `notify:read` 控制页面访问；以 `notify:write` 控制渠道/事件映射保存；以 `notify:test` 控制测试发送按钮。保留现有 UI 风格和布局。

- [ ] **Step 5: 更新角色页 admin/内置角色行为**

admin 不显示编辑入口；其他内置角色显示编辑入口并允许修改权限矩阵；所有内置角色不显示删除入口。更新提示文案和提交 payload，不引入新的视觉组件或样式。

- [ ] **Step 6: 运行前端验证**

Run: `npm.cmd run type-check` and `npm.cmd run build` in `frontend/`.

Expected: both commands PASS。

### Task 5: 清理开发阶段旧权限数据并完成全量验证

**Files:**
- Modify only if required by tests/docs: `backend/tests/conftest.py`, `docs/01-产品需求文档PRD.md`, `docs/04-API设计.md`

- [ ] **Step 1: 更新测试种子断言**

确保测试 fixture 从新的 `PERMISSIONS` 和 `BUILTIN_ROLES` 建立数据，不保留 `notify:config`、`ticket:withdraw` 等旧权限。

- [ ] **Step 2: 执行后端全量测试**

Run: `python -m pytest -q` in `backend/`.

Expected: PASS。

- [ ] **Step 3: 执行前端全量验证**

Run: `npm.cmd run type-check` and `npm.cmd run build` in `frontend/`.

Expected: PASS。

- [ ] **Step 4: 核对改动范围**

Run: `git diff --stat` and `git status --short`.

Expected: only RBAC permission constants, seed/service/API tests, frontend permission checks, and directly synchronized RBAC docs are changed; no UI style files or unrelated modules are modified.
