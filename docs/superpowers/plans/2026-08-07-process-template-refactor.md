# 独立流程模板重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前“工单模板内嵌步骤/参数/审批/执行策略”的模型重构为可复用的独立流程模板，并支持步骤前单角色审批、作业主机动态参数预生成和最终工单快照。

**Architecture:** 新增 `ProcessTemplate`/`ProcessStep` 作为流程定义，`TicketTemplate` 只保留业务入口并一对一引用一个流程模板；一个流程模板可被多个工单模板引用。流程没有版本，启停控制新提交，工单提交时固化完整流程和最终参数快照；历史工单不依赖实时流程模板。

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic + pytest；Vue 3 + TypeScript + Ant Design Vue + CodeMirror；现有 asyncssh 作业主机执行通道；现有 Redis/worker 执行队列。

---

## 预置约束

- 流程步骤严格串行；每个步骤最多一个审批角色；该角色任意一人通过即继续，驳回即终止。
- 流程级参数来源为 `fixed`、`user`、`generated`；用户参数支持文本和单选枚举，默认值由服务端校验。
- 每个流程最多一个动态参数脚本。脚本在工单模板指定的作业主机上执行，可访问外部网络；输出固定值或候选列表。
- 预生成结果只作为临时提交上下文，不写审计、不写执行日志；最终参数值写入工单快照，创建人和审批人可查看。
- 流程停用后仍可编辑；停用阻止新提交但不影响已提交工单；被任意工单模板引用时不可删除；只有历史工单引用时允许删除。
- 工单模板和流程模板均不配置凭据引用；作业主机连接凭据继续由 `job_host` 自身管理。
- 项目尚未发布，允许重建开发数据库，不实现旧接口/旧数据兼容迁移。
- 前端 UI 必须保持当前系统设计风格：优先复用现有页面的布局、颜色、字体、间距、表格、抽屉、弹窗、标签、图标和交互模式，不引入新的视觉体系或营销型页面；`ui-ux-pro-max` 只用于补充可访问性、加载反馈、表单校验和响应式检查。

## Task 1: 重建领域模型和开发数据库结构

**Files:**
- Modify: `backend/app/models/job.py`
- Modify: `backend/app/models/ticket.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/0010_process_template_refactor.py`
- Test: `backend/tests/test_job_api.py`
- Test: `backend/tests/test_ticket_api.py`

- [ ] **Step 1: 写模型约束测试**

在 `test_job_api.py` 增加测试数据构造约束：流程模板拥有流程级参数和步骤审批角色；工单模板只提交 `process_template_id`，请求中不存在 `credential_refs`、`steps`、`approval_nodes` 和流程版本字段。先运行：

```bash
cd backend
python -m pytest tests/test_job_api.py -q
```

预期：新断言因旧响应结构而失败。

- [ ] **Step 2: 定义 SQLAlchemy 模型**

新增 `ProcessTemplate` 和 `ProcessStep`：

```python
class ProcessTemplate(Base):
    __tablename__ = "process_template"
    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="enabled", nullable=False)
    params_schema: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    generator_script: Mapped[str | None] = mapped_column(MTEXT)
    generator_timeout: Mapped[int | None] = mapped_column(Integer)
    exec_strategy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

class ProcessStep(Base):
    __tablename__ = "process_step"
    id: Mapped[int] = pk_column()
    process_template_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    script_type: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(MTEXT, nullable=False)
    timeout: Mapped[int] = mapped_column(Integer, default=600, nullable=False)
    approval_role_id: Mapped[int | None] = mapped_column(UBIGINT)
```

给 `ProcessStep` 增加 `(process_template_id, step_order)` 唯一约束。

- [ ] **Step 3: 收敛现有工单模型**

在 `TicketTemplate` 增加 `process_template_id` 和对应索引，移除内嵌步骤、审批、执行策略、凭据引用和 `current_version` 字段；保留工单业务入口字段、通知、可见角色、状态及现有撤回策略。`Ticket` 移除模板版本快照和凭据快照，增加流程名称/来源所需的快照字段，保留 `flow_snap` 作为完整 JSON 快照，并将 `current_node` 改为 `current_step`。`TicketApproval.node_order` 改为 `step_order`。

流程来源 ID 不建立阻止删除流程的强外键；历史工单以 `flow_snap` 为事实来源，流程删除后仍能展示完整流程。

- [ ] **Step 4: 编写 0010 Alembic 结构迁移**

创建新表、增加引用列和临时预生成表 `ticket_parameter_prepare`（token、模板 ID、输入参数、生成值、候选项、过期时间、消费时间）；删除旧的 `template_step`、`template_approval_node`、`template_version` 及工单模板/工单中的废弃列。开发数据库允许直接重建，不做旧数据转换。

- [ ] **Step 5: 运行结构和现有测试**

```bash
cd backend
python -m pytest tests/test_job_api.py tests/test_ticket_api.py -q
```

预期：模型字段测试通过；业务实现相关测试暂时失败，作为后续任务的红灯基线。

## Task 2: 重写参数、流程和工单模板 Schema

**Files:**
- Modify: `backend/app/schemas/job.py`
- Modify: `backend/app/schemas/ticket.py`
- Create: `backend/app/schemas/process_template.py`
- Create: `backend/app/schemas/ticket_template.py`
- Test: `backend/tests/test_job_api.py`

- [ ] **Step 1: 写 Schema 验证测试**

覆盖以下输入：参数名重复、固定参数没有值、枚举没有选项、动态参数输出字段未声明、步骤审批角色为空、流程无步骤、脚本超时超界。

- [ ] **Step 2: 定义流程参数 Schema**

参数统一使用以下结构：

```json
{
  "name": "environment",
  "label": "环境",
  "source": "user",
  "input_type": "enum",
  "options": ["test", "prod"],
  "default": "test",
  "required": true,
  "description": "部署环境"
}
```

`source` 只允许 `fixed/user/generated`；`input_type` 只允许 `text/enum`；枚举只允许单选。生成结果另外校验为固定值或候选列表。

- [ ] **Step 3: 定义流程和工单模板请求体**

流程模板请求体包含 `name`、`description`、`params_schema`、可选动态脚本、`exec_strategy` 和步骤数组；步骤数组包含 `approval_role_id`。工单模板请求体只包含名称、类型、描述、作业主机、`process_template_id`、通知、可见角色和业务状态字段，不包含任何凭据引用或流程版本字段。

- [ ] **Step 4: 运行 Schema 测试**

```bash
cd backend
python -m pytest tests/test_job_api.py -q
```

预期：新增校验测试通过。

## Task 3: 实现流程模板和工单模板服务/API

**Files:**
- Create: `backend/app/services/process_template_service.py`
- Modify: `backend/app/services/template_service.py`
- Create: `backend/app/api/v1/process_templates.py`
- Modify: `backend/app/api/v1/templates.py`
- Modify: `backend/app/api/v1/__init__.py`
- Test: `backend/tests/test_process_template_api.py`
- Test: `backend/tests/test_job_api.py`

- [ ] **Step 1: 写流程模板 API 失败测试**

覆盖：创建/详情/更新、启停、引用统计、被引用时删除 422、无工单模板引用时删除成功、停用后仍可编辑。

- [ ] **Step 2: 实现流程模板 CRUD**

新增 `/process-templates` 路由：

```text
GET    /process-templates
POST   /process-templates
GET    /process-templates/{id}
PUT    /process-templates/{id}
PUT    /process-templates/{id}/status
DELETE /process-templates/{id}
```

更新采用当前配置全量替换，不创建版本；详情返回引用工单模板摘要和当前步骤/参数/脚本。

- [ ] **Step 3: 改造 `/templates` 为工单模板 API**

工单模板创建/更新时校验作业主机存在且启用、流程模板存在、可见角色存在；允许保留对已停用流程的现有引用，但新的流程选择列表只返回启用流程。删除流程模板前查询全部工单模板引用，包含已停用工单模板。

- [ ] **Step 4: 运行 API 测试**

```bash
cd backend
python -m pytest tests/test_process_template_api.py tests/test_job_api.py -q
```

预期：流程模板生命周期、引用保护和无凭据字段断言全部通过。

## Task 4: 实现作业主机动态参数预生成

**Files:**
- Create: `backend/app/services/parameter_prepare_service.py`
- Modify: `backend/app/engine/ssh_runner.py`
- Modify: `backend/app/models/ticket.py`
- Modify: `backend/app/api/v1/tickets.py`
- Modify: `backend/app/schemas/ticket.py`
- Test: `backend/tests/test_parameter_prepare.py`
- Test: `backend/tests/test_ticket_api.py`

- [ ] **Step 1: 写动态参数失败测试**

覆盖：固定值直接返回、脚本返回候选列表、候选单选、用户输入变化后旧 token 失效、非法 JSON、未声明字段、脚本超时、脚本失败不创建工单、预生成记录不出现在审计查询中。

- [ ] **Step 2: 为 SSH runner 增加无日志 JSON 捕获函数**

复用 `open_connection` 和作业主机凭据，但不使用 `LogChannel`：在内存中收集 stdout/stderr，按超时和退出码返回结果；成功时只解析 stdout JSON，失败时返回用户可读错误摘要，不写审计或执行日志。

- [ ] **Step 3: 实现 prepare 服务**

执行顺序固定为：校验工单模板和流程状态 → 校验用户输入 → 组装固定/用户参数 → 连接作业主机执行生成脚本 → 校验生成固定值/候选列表 → 写入短期 `ticket_parameter_prepare` → 返回 `prepare_id` 和过期时间。生成脚本接收 JSON，不允许覆盖固定值和用户输入字段。

- [ ] **Step 4: 增加 API**

```text
POST /tickets/prepare
  输入：{template_id, params}
  输出：{prepare_id, values, options, expires_at}

POST /tickets
  输入：{template_id, params, prepare_id?}
```

创建工单时服务端校验 `prepare_id` 未过期、输入参数哈希一致、候选选择属于原列表，并在同一事务内消费预生成结果和创建工单快照。

- [ ] **Step 5: 运行动态参数测试**

```bash
cd backend
python -m pytest tests/test_parameter_prepare.py tests/test_ticket_api.py -q
```

预期：成功结果可创建快照，所有脚本失败/过期/篡改场景不产生工单。

## Task 5: 重写工单快照和审批状态机

**Files:**
- Modify: `backend/app/services/ticket_service.py`
- Modify: `backend/app/api/v1/tickets.py`
- Modify: `backend/app/engine/pipeline.py`
- Modify: `backend/app/services/execution_service.py`
- Modify: `backend/app/models/ticket.py`
- Test: `backend/tests/test_ticket_api.py`
- Test: `backend/tests/test_execution_engine.py`

- [ ] **Step 1: 写状态机失败测试**

覆盖：流程无审批直接入队、首个有审批角色的步骤进入 `approving`、角色任一成员通过后执行目标步骤、非当前步骤角色不可审批、驳回立即终止、并发审批只有第一条有效、审批后续步骤继续按顺序处理。

- [ ] **Step 2: 实现完整流程快照**

快照统一为对象：

```json
{
  "process_name": "生产发布流程",
  "params_schema": [],
  "params": {},
  "generator": {"script": "...", "result": {}},
  "exec_strategy": {},
  "steps": [
    {"step_order": 1, "name": "检查", "content": "...", "timeout": 600, "approval_role_id": null}
  ]
}
```

快照不包含凭据引用；作业执行继续通过作业主机自身配置取得连接凭据。

- [ ] **Step 3: 改造审批推进**

用 `current_step` 表示当前待审批/执行步骤；审批查询按 `current_step` 对应的 `approval_role_id` 过滤。通过后只推进到目标步骤执行；驳回将工单置为 `rejected`，不创建后续执行；所有审批操作保留现有审批记录和通知逻辑。

- [ ] **Step 4: 清理执行引擎凭据引用路径**

从 `pipeline.py` 和执行详情中移除模板级 `credential_refs` 读取/注入，保留作业主机登录凭据的现有使用路径；执行策略从流程快照读取。

- [ ] **Step 5: 运行后端核心测试**

```bash
cd backend
python -m pytest tests/test_ticket_api.py tests/test_execution_engine.py -q
```

预期：快照、审批、执行队列和凭据字段清理测试全部通过。

## Task 6: 重构前端模板管理为两个页签

**Files:**
- Create: `frontend/src/views/job/TemplateManagement.vue`
- Create: `frontend/src/views/job/ProcessTemplateList.vue`
- Create: `frontend/src/views/job/ProcessTemplateEditor.vue`
- Modify: `frontend/src/views/job/TemplateList.vue`
- Modify: `frontend/src/views/job/TemplateEditor.vue`
- Modify: `frontend/src/views/job/TemplateRuleView.vue`
- Modify: `frontend/src/views/job/meta.ts`
- Modify: `frontend/src/api/job.ts`
- Modify: `frontend/src/router/index.ts`

- [ ] **Step 1: 更新 TypeScript API 类型**

删除模板版本、审批节点和凭据引用类型；新增流程模板、流程步骤、参数来源、生成脚本和流程引用类型；为工单模板 API 增加 `process_template_id` 和流程摘要。

- [ ] **Step 2: 创建页签容器**

在现有模板管理路由中使用 Ant Design Vue `a-tabs`，页签为“工单模板”和“流程模板”。以现有 `TemplateList.vue`、`TemplateEditor.vue`、`TicketWizard.vue` 和公共样式变量为视觉基准，保持当前系统的页面宽度、密度、颜色、字体、间距、表格列操作、抽屉/弹窗结构、标签颜色和图标用法；优先复用现有组件和 CSS token，不新建独立设计系统。补充语义化按钮、可见键盘焦点、加载反馈、行内错误提示和窄屏表单适配；不引入营销型大卡片或装饰性动画。

- [ ] **Step 3: 实现流程模板列表和编辑器**

流程列表显示名称、状态、步骤数、引用工单模板数和更新时间；支持编辑、启停、删除。编辑器分为基本信息、流程参数、动态脚本、步骤编排、执行策略；每个步骤直接配置一个审批角色，停用状态仍允许编辑。

- [ ] **Step 4: 精简工单模板编辑器**

移除步骤、全局参数、执行策略、审批节点和凭据引用；保留基本信息、作业主机、流程模板选择、通知、可见角色和业务状态。流程选择只展示启用流程；已有停用流程引用显示警告但可打开编辑。

- [ ] **Step 5: 运行前端类型检查**

```bash
cd frontend
npm run type-check
```

预期：删除旧字段后的所有组件类型检查通过。

## Task 7: 重构工单提交、审批待办和详情交互

**Files:**
- Modify: `frontend/src/api/ticket.ts`
- Modify: `frontend/src/views/ticket/TicketWizard.vue`
- Modify: `frontend/src/views/ticket/TicketList.vue`
- Modify: `frontend/src/views/ticket/TicketDetailDrawer.vue`
- Modify: `frontend/src/views/ticket/TodoList.vue`
- Modify: `frontend/src/views/ticket/meta.ts`

- [ ] **Step 1: 增加预生成 API 类型和状态**

新增 `prepareTicket`、`createTicket` 的 `prepare_id` 参数、动态固定值/候选列表类型、最终参数来源类型；移除模板版本、节点计数和凭据引用字段。

- [ ] **Step 2: 改造提交向导**

用户填写文本/静态枚举后点击预生成；页面显示“正在作业主机上生成参数”的加载状态。固定值只读展示，候选列表使用单选；用户确认后提交快照。修改前置参数会清空旧结果并重新预生成；失败提示紧邻表单，不创建工单。

- [ ] **Step 3: 改造工单列表和待办**

列表展示流程名称、当前步骤和审批角色，不再展示模板版本/节点序号。待办按当前步骤展示“步骤 N 前审批”，审批操作保持通过/驳回和评论必填规则。

- [ ] **Step 4: 改造工单详情**

展示最终参数及来源标签（固定、用户输入、系统生成、用户从动态列表选择）、流程快照、步骤前审批门禁、审批时间线和执行日志。流程已删除时使用快照名称并显示“流程已删除”，不请求实时流程详情。

- [ ] **Step 5: 运行前端构建**

```bash
cd frontend
npm run type-check
npm run build
```

预期：类型检查和生产构建均通过。

## Task 8: 更新种子、文档和开发测试数据

**Files:**
- Modify: `backend/app/services/seed.py`
- Modify: `backend/tests/conftest.py`
- Modify: `docs/01-产品需求文档PRD.md`
- Modify: `docs/03-数据库设计.md`
- Modify: `docs/04-API设计.md`
- Modify: `docs/05-开发任务拆解.md`
- Modify: `frontend/src/views/job/meta.ts`

- [ ] **Step 1: 重写开发种子**

创建至少一条可复用流程和两个引用它的工单模板；覆盖固定参数、文本参数、枚举参数、步骤前审批。删除旧的模板版本和多级审批种子，不写入任何工单/流程凭据引用。

- [ ] **Step 2: 同步数据库和 API 文档**

文档明确流程/工单模板字段边界、启停/删除规则、动态参数预生成协议、快照结构和审批状态；删除旧版本、多节点、凭据引用字段说明。

- [ ] **Step 3: 运行全量后端测试**

```bash
cd backend
python -m pytest -q
```

预期：全量后端测试通过，且测试响应中不再出现工单模板凭据引用字段。

## Task 9: 集成验证和本地验收

**Files:**
- No additional source files; verify all files from Tasks 1-8.

- [ ] **Step 1: 检查变更范围和废弃引用**

```bash
rg -n "template_version|current_version|approval_nodes|current_node|credential_refs" backend/app frontend/src docs
```

预期：仅保留与凭据实体/作业主机连接管理相关的合法引用；工单模板、流程模板和工单快照不再使用凭据引用或版本/多节点字段。

- [ ] **Step 2: 执行后端和前端验证**

```bash
cd backend
python -m pytest -q
cd ../frontend
npm run type-check
npm run build
```

- [ ] **Step 3: 启动前端开发服务进行人工验收**

```bash
cd frontend
npm run dev
```

验收路径：模板管理两个页签 → 新建流程 → 新建两个工单模板引用流程 → 提交动态参数预生成 → 单选候选值 → 审批步骤前待办 → 任意角色成员通过/驳回 → 查看最终参数、流程快照和执行日志 → 停用/编辑/重新启用流程 → 验证删除引用保护。

## 计划自检

- 业务方案覆盖：独立流程复用、流程无版本、启停与删除、流程级参数、作业主机动态脚本、固定值/候选值、步骤前单角色审批、最终快照和无凭据引用。
- UI 方案覆盖：模板管理双页签、密集后台布局、可见焦点、语义化按钮、加载反馈、行内错误和移动/窄屏表单可用性。
- 测试覆盖：Schema、CRUD 生命周期、引用保护、动态脚本失败/过期/篡改、快照、审批并发、执行回归、类型检查和生产构建。
- 计划不包含生产数据兼容迁移、旧版本回看、会签/多级审批和流程分支。
