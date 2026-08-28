# OpsPilot 协作指南

## 项目概览

OpsPilot 是面向企业内网的自动化运维平台。后端使用 FastAPI、异步 SQLAlchemy、MySQL 8、Redis 7 和 `asyncssh`；前端使用 Vue 3、TypeScript、Ant Design Vue、Pinia 和 Vite；生产形态是单机 Docker Compose。

业务链路为：CMDB 维护主机/应用资产台账，作业主机绑定 SSH 凭据，工单模板提供提交入口和参数，流程模板提供串行步骤/策略/步骤前审批，工单提交时生成快照，Worker 在作业主机上执行并写入日志，通知和审计记录全生命周期。

## 目录边界

- `backend/app/main.py`：API 进程入口；`backend/app/worker_main.py`：执行 Worker 入口。
- `backend/app/api/v1/`：REST 路由；`backend/app/models/`：数据库模型；`backend/alembic/versions/`：迁移。
- `frontend/src/`：Vue 页面、API 封装和状态管理。
- `deploy/`：Compose、Nginx、初始化 SQL、备份和离线镜像脚本。
- `docs/`：当前产品、架构、数据、API、任务和模板设计。

## 本地验证

后端在 `backend/` 执行：

```bash
python -m pytest -q
python -m pytest tests/test_ticket_api.py -q
```

前端在 `frontend/` 执行：

```bash
npm install
npm run dev
npm run type-check
npm run build
```

Windows PowerShell 若 `npm.ps1` 被执行策略拦截，使用 `npm.cmd` 替代 `npm`。集成部署从 `deploy/` 执行 `docker compose up -d --build`；API 启动时执行迁移和幂等种子，Worker 等待 API 健康后启动。

## 修改规范

- 编码前先复述需求；有歧义时列出解释并询问，不自行决定业务边界。
- 只修改任务直接涉及的文件，保留无关工作区改动；不要提交 `.env`、凭据、JWT/MFA 密钥或私钥。
- 认证、凭据、权限、审计和公开 API 属于安全敏感改动，必须补充针对性测试或明确验证步骤。
- 新增或修改类、接口和关键方法时，用简洁中文注释说明原因与核心逻辑；不为显而易见的代码添加噪声注释。
- 提交使用 Conventional Commits，例如 `fix(notify): 修复通知重试`、`feat(ui): 增加执行筛选`、`docs: 更新部署说明`。

## 文档同步

修改 API、数据模型、配置变量、权限或部署行为时，同步更新 `docs/`、根 `README.md` 或 `deploy/DEPLOYMENT.md`。文档只描述当前实现的功能、接口和数据模型。
