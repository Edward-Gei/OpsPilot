# OpsPilot V1 精简版

自动化运维平台：CMDB 资产台账（主机/应用）＋ 作业模板（作业主机 + Shell/Ansible 多步 Pipeline）→
工单审批 → 作业主机上步骤串行执行（实时日志/执行控制）→ 通知（邮件/Webhook/Teams）→ 全链路审计。
面向单机 Docker Compose 内网部署。

- 技术栈：FastAPI + SQLAlchemy(async) + MySQL 8 + Redis 7 | Vue3 + TS + Ant Design Vue 4 | asyncssh
- 设计文档：[docs/](docs/)（PRD / 技术架构 / 数据库 / API / 任务拆解）

## 一、快速部署（一键启动）

> 生产环境完整手册（前置条件/安全基线/HTTPS/升级回滚/故障排查）：
> [deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md)

前置：部署机已装 Docker Engine 24+（含 compose 插件），无需外网（离线部署见第四节）。

```bash
cd deploy
cp .env.example .env
# 必改三项：MYSQL_ROOT_PASSWORD / MYSQL_PASSWORD / JWT_SECRET_KEY
# 建议同时设置 SECRET_ENCRYPT_KEY（openssl rand -base64 32），用于 MFA/SSH 凭据字段级加密
docker compose up -d --build
```

启动完成后访问 `http://<部署机IP>:8080`（端口由 `NGINX_HTTP_PORT` 控制）。
五容器：nginx（入口/静态资源）、api（REST + 长轮询）、worker（执行引擎）、mysql、redis。

### 初始化说明

- 数据库结构：api 容器启动时自动执行 Alembic 迁移（`RUN_MIGRATIONS=1`），无需手工建表。
- 初始管理员：用户名 `admin`；密码取 `.env` 的 `ADMIN_INITIAL_PASSWORD`，
  留空则随机生成并打印到 api 容器日志（`docker logs opspilot-api | grep 初始`）。
  首次登录强制改密 + 绑定 MFA（TOTP）。
- 首次使用顺序（对应 PRD DoD）：
  1. 系统配置 → 凭据管理：录入 SSH 凭据（密码/密钥）
  2. CMDB → 主机管理：手工录入或 Excel 批量导入（模板页内下载，单次 ≤5000 行）
  3. CMDB → 应用管理：建应用并关联主机（多对多）
  4. 系统配置 → 作业主机：录入作业主机（IP/端口/关联凭据/工作目录）并测试连通性（模板前置）
  5. 作业模板：选定作业主机、编排多步 Pipeline（Shell/Ansible）、执行策略、审批节点、通知规则、凭据引用（可选）
  6. 通知配置：启用邮件/Webhook/Teams 渠道并发送测试消息
  7. 工单：选模板提交 → 审批 → 实时日志观察执行 → 通知送达

### 常用运维命令

```bash
docker compose ps                  # 状态
docker logs -f opspilot-worker     # 执行引擎日志
docker compose down                # 停止（数据卷保留）
docker exec -e PYTHONPATH=/app -w /app opspilot-api python /tmp/smoke_v2.py  # 冒烟（先 docker cp deploy/smoke_v2.py）
```

## 二、备份与恢复

```bash
bash deploy/scripts/backup.sh      # 产物：deploy/backup/<时间戳>/{mysql_opspilot.sql, exec_logs.tar.gz}
```

- MySQL：`mysqldump --single-transaction` 全库逻辑备份（不锁表）；恢复命令见脚本头注释。
- 执行日志卷 `exec-logs`：tar 归档（执行历史日志文件）。
- Redis 仅作队列/缓存（AOF 已开），无需纳入备份。
- 建议：备份脚本加入部署机 crontab（如每日 02:00），产物异地留存。

## 三、配置清单（deploy/.env.example 为唯一来源）

| 变量 | 必改 | 说明 |
| --- | :---: | --- |
| `NGINX_HTTP_PORT` | | 对外 HTTP 端口，默认 8080 |
| `MYSQL_ROOT_PASSWORD` / `MYSQL_PASSWORD` | ✔ | 数据库 root/业务账号密码 |
| `MYSQL_HOST/PORT/USER/DB` | | 默认指向 compose 内 mysql |
| `REDIS_HOST/PORT/DB` | | 默认指向 compose 内 redis（内网无密码） |
| `JWT_SECRET_KEY` | ✔ | JWT 签名密钥（openssl rand -hex 32） |
| `SECRET_ENCRYPT_KEY` | 建议 | AES-256-GCM 主密钥；留空则从 JWT 密钥派生并告警 |
| `ACCESS_TOKEN_MINUTES` 等 4 项 | | 令牌时效（15min/7d/5min/10min） |
| `ADMIN_INITIAL_PASSWORD` | | 初始 admin 密码，留空随机生成打日志 |
| `AUDIT_RETENTION_DAYS` | | 审计保留天数（默认 365，凌晨任务清理过期分区） |
| `EXEC_LOG_DIR` | | 执行日志落盘目录（api/worker 共享卷） |
| `EXEC_GLOBAL_CONCURRENCY_DEFAULT` | | 全局 SSH 并发默认值（首启写入系统配置，后续以页面为准） |
| `CORS_ORIGINS` | | 仅本地开发 vite 直连时用；生产 nginx 同源无需修改 |
| `DEBUG` | | 生产保持 false |

## 四、离线部署（纯内网，DoD-6）

```bash
# 有外网的构建机
bash deploy/scripts/export_images.sh export   # -> deploy/offline/opspilot_images.tar.gz
# 将 tar 包 + deploy/ 目录拷贝至内网部署机
bash deploy/scripts/export_images.sh import   # docker load
cd deploy && cp .env.example .env             # 改密钥后
docker compose up -d                          # 不带 --build，直接用导入镜像
```

## 五、压测与验收

- 压测设施（500 台 sshd 矩阵 + mailpit + webhook-echo）：[deploy/loadtest/](deploy/loadtest/)
- 实测报告（列表接口 P95 全部 <500ms；500 台单工单基线为执行范式改造前的历史数据）：
  [deploy/loadtest/LOADTEST_REPORT.md](deploy/loadtest/LOADTEST_REPORT.md)
- 后端测试：`docker exec opspilot-api python -m pytest tests/ -q`

## 六、目录结构

```
backend/    FastAPI 应用（api 与 worker 共用镜像，入口不同）
frontend/   Vue3 前端（构建产物打进 nginx 镜像）
deploy/     compose 编排 / nginx.conf / mysql 初始化 / 备份与离线脚本 / 压测设施 / 生产部署手册
docs/       设计文档（PRD、技术架构、数据库、API、任务拆解）
```
