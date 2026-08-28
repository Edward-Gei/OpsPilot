# OpsPilot V1

OpsPilot 是面向企业内网的自动化运维平台：CMDB 资产台账、作业主机、工单模板、可复用流程模板、审批、串行执行、实时日志、通知和审计组成完整闭环。

技术栈：FastAPI + 异步 SQLAlchemy + MySQL 8 + Redis 7 + `asyncssh`；Vue 3 + TypeScript + Ant Design Vue 4；Docker Compose 单机部署。

设计文档见 [`docs/`](docs/)，生产和内网部署见 [`deploy/DEPLOYMENT.md`](deploy/DEPLOYMENT.md)。

## 快速部署

前置条件：部署机安装 Docker Engine 24+ 和 Compose v2。在线部署不需要单独安装 Python、Node、MySQL 或 Redis。

```bash
cd deploy
cp .env.example .env
# 修改 MYSQL_ROOT_PASSWORD、MYSQL_PASSWORD、JWT_SECRET_KEY
# 生产环境同时设置 SECRET_ENCRYPT_KEY 并离线保存
docker compose up -d --build
```

启动后访问 `http://<部署机IP>:8080`，端口由 `NGINX_HTTP_PORT` 控制。Compose 启动五个服务：`nginx`、`api`、`worker`、`mysql`、`redis`。只有 Nginx 暴露宿主机端口，API、MySQL、Redis 只在 Compose 网络内通信。

API 容器启动时执行 `alembic upgrade head` 并幂等初始化权限、内置角色、初始 admin、系统配置和通知默认映射。`ADMIN_INITIAL_PASSWORD` 为空时随机生成密码并写入 API 日志；首次登录必须修改密码并按策略完成 MFA。

## 首次使用

1. 系统配置中创建 SSH 凭据。
2. 创建作业主机并绑定凭据，测试连通性。
3. 在 CMDB 维护主机和应用资产。
4. 创建流程模板，配置 Shell/Playbook 步骤、步骤超时、失败策略和步骤前审批角色。
5. 创建工单模板，绑定作业主机和流程模板，配置参数、动态生成脚本、通知规则和可见角色。
6. 配置邮件、Webhook 或 Teams 渠道及事件映射。
7. 选择启用的工单模板提交，按流程审批，观察执行日志和通知记录。

## 常用运维命令

```bash
cd deploy
docker compose ps
docker logs -f opspilot-api
docker logs -f opspilot-worker
docker compose down                 # 保留数据卷
docker compose up -d
curl -s http://localhost:8080/healthz
curl -s http://localhost:8080/api/v1/ping
```

备份数据库和执行日志：

```bash
bash scripts/backup.sh
```

备份产物位于 `deploy/backup/<时间戳>/`。Redis 仅保存队列和缓存，不作为业务备份对象。

## 离线部署

在可联网构建机执行：

```bash
cd deploy
bash scripts/export_images.sh export
```

将生成的 `offline/opspilot_images.tar.gz` 和整个 `deploy/` 目录复制到内网部署机，执行：

```bash
cd deploy
bash scripts/export_images.sh import
cp .env.example .env
# 修改密钥
docker compose up -d
```

## 目录

```text
backend/  FastAPI API、数据库模型、迁移和 Worker
frontend/ Vue 3 单页应用，构建后由 Nginx 提供静态资源
deploy/   Compose、Nginx、初始化 SQL、备份和离线镜像脚本
docs/     产品、架构、数据库、API、任务和模板设计
```
