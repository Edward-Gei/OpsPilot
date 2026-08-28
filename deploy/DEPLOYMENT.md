# OpsPilot V1 部署手册

适用形态：单机 Docker Compose，在线或纯内网部署。本文只描述当前 `deploy/` 文件可执行的方式；部署变量以 [`.env.example`](.env.example) 为准。

## 1. 组件和数据卷

| 服务 | 作用 | 宿主机端口 |
| --- | --- | --- |
| `opspilot-nginx` | Vue 静态资源、`/api/` 反代、健康检查 | ${NGINX_HTTP_PORT:-8080} |
| `opspilot-api` | FastAPI、迁移、业务 API | 不暴露 |
| `opspilot-worker` | Redis 队列消费、SSH 执行、通知和定时任务 | 不暴露 |
| `opspilot-mysql` | MySQL 8 业务数据库 | 不暴露 |
| `opspilot-redis` | Redis 7 队列和缓存，AOF | 不暴露 |

Compose 使用三个命名卷：`mysql-data` 保存数据库，`exec-logs` 保存执行日志，`redis-data` 保存 Redis AOF。只有 Nginx 对外提供入口。启动顺序为 MySQL/Redis 健康 -> API（迁移和种子）健康 -> Worker/Nginx。

## 2. 前置条件

- Docker Engine 24+，且 `docker compose version` 可用。
- 在线构建需要访问 Docker 镜像和依赖源；离线部署按第 5 节导入镜像。
- 部署机磁盘至少 40 GB，执行日志量较大时预留更多空间。
- Worker 到作业主机需要 SSH 出站；通知按渠道需要 SMTP、Webhook 或 Teams 出站。
- 对外只放行 `NGINX_HTTP_PORT`，默认 8080。

## 3. 安全配置

```bash
cd deploy
cp .env.example .env
```

上线前至少修改：

| 变量 | 要求 |
| --- | --- |
| `MYSQL_ROOT_PASSWORD` | MySQL root 强密码；仅新数据卷初始化时读取 |
| `MYSQL_PASSWORD` | 业务账号强密码；仅新数据卷初始化时读取 |
| `JWT_SECRET_KEY` | 用 `openssl rand -hex 32` 生成 |
| `SECRET_ENCRYPT_KEY` | 建议用 `openssl rand -base64 32` 生成并离线保存；丢失会无法解密已存凭据 |
| `DEBUG` | 生产保持 `false` |
| `ADMIN_INITIAL_PASSWORD` | 可留空随机生成，或设置一次性初始密码 |
| `PUBLIC_BASE_URL` | 密码重置邮件中的前端访问地址 |

`.env` 只存部署机，建议 `chmod 600 .env`，不要提交到版本库。Redis 默认只在 Compose 内网无密码，不映射宿主端口。

`.env.example` 变量参考：

| 变量 | 用途和默认值 |
| --- | --- |
| `NGINX_HTTP_PORT` | 宿主机 HTTP 端口，默认 `8080` |
| `MYSQL_ROOT_PASSWORD` | MySQL root 密码；仅首次初始化 `mysql-data` 卷时读取 |
| `MYSQL_HOST` / `MYSQL_PORT` | API 连接 MySQL 的主机和端口，默认 `mysql:3306` |
| `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DB` | API 业务账号、密码和数据库名 |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` | Redis 连接地址，默认 `redis:6379/0` |
| `JWT_SECRET_KEY` | JWT 签名密钥，生产环境必须替换 |
| `SECRET_ENCRYPT_KEY` | AES-256-GCM 字段加密密钥；留空时从 JWT 密钥派生并告警 |
| `ACCESS_TOKEN_MINUTES` / `REFRESH_TOKEN_DAYS` | Access/Refresh Token 有效期，默认 `15` 分钟/`7` 天 |
| `MFA_TOKEN_MINUTES` / `PWD_CHANGE_TOKEN_MINUTES` | MFA 挑战和强制改密令牌有效期，默认 `5`/`10` 分钟 |
| `RESET_TOKEN_TTL_MINUTES` | 密码重置令牌有效期，默认 `30` 分钟 |
| `PUBLIC_BASE_URL` | 密码重置邮件使用的前端公共地址 |
| `ADMIN_INITIAL_PASSWORD` | 初始 admin 密码；留空则随机生成并写入 API 日志 |
| `AUDIT_RETENTION_DAYS` | 审计数据保留天数，默认 `365` |
| `EXEC_LOG_DIR` | API/Worker 共享的执行日志目录，默认 `/data/logs` |
| `EXEC_GLOBAL_CONCURRENCY_DEFAULT` | 首次初始化的全局执行并发默认值，默认 `50` |
| `CORS_ORIGINS` | 本地 Vite 直连 API 时的允许来源，生产同源部署通常无需修改 |
| `DEBUG` | 调试开关，生产必须为 `false` |

## 4. 在线部署

```bash
cd /opt/opspilot/deploy
cp .env.example .env
# 编辑 .env，完成第 3 节安全配置
docker compose up -d --build
docker compose ps
```

等待 `mysql`、`api` 显示 healthy，查看初始化日志：

```bash
docker logs --tail 200 opspilot-api
```

浏览器访问 `http://<部署机IP>:8080`。API 会自动执行 Alembic 迁移和幂等种子；admin 首次登录需要改密并完成 MFA 策略要求。

## 5. 离线部署

在可联网构建机：

```bash
cd <代码目录>/deploy
bash scripts/export_images.sh export
```

将 `offline/opspilot_images.tar.gz` 和整个 `deploy/` 目录复制到内网部署机。在内网部署机执行：

```bash
cd /opt/opspilot/deploy
bash scripts/export_images.sh import
cp .env.example .env
# 编辑 .env
docker compose up -d
docker compose ps
```

导入脚本加载 `opspilot-api`、`opspilot-worker`、`opspilot-nginx`、`mysql:8.0` 和 `redis:7-alpine` 五个镜像，不带 `--build`。

## 6. 部署验证

```bash
cd deploy
docker compose ps
curl -s http://localhost:8080/healthz
curl -s http://localhost:8080/api/v1/ping
```

页面验证：打开入口，使用初始 admin 登录，完成首次改密和 MFA，然后按“凭据 -> 作业主机 -> 流程模板 -> 工单模板 -> 通知 -> 提单审批执行”的顺序初始化。

## 7. 日常运维

```bash
docker compose ps
docker logs -f opspilot-api
docker logs -f opspilot-worker
docker logs --tail 200 opspilot-nginx
docker compose restart worker
docker compose down
docker compose up -d
```

实时日志使用 API 长轮询，Nginx 已配置 120 秒 API 读取超时。外部反向代理若存在，也应允许至少 120 秒读取超时。

## 8. 备份与恢复

在部署机执行：

```bash
cd /opt/opspilot/deploy
bash scripts/backup.sh
```

产物为 `backup/<时间戳>/mysql_opspilot.sql` 和 `exec_logs.tar.gz`。脚本备份 MySQL 全库及 `exec-logs` 卷；Redis 是可重建队列/缓存，不纳入业务备份。

恢复前停止 API/Worker，确认目标环境后执行：

```bash
docker exec -i opspilot-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' < backup/<时间戳>/mysql_opspilot.sql
docker run --rm -v opspilot_exec-logs:/data -v /绝对路径/backup/<时间戳>:/backup alpine tar xzf /backup/exec_logs.tar.gz -C /data
docker compose restart api worker
```

恢复命令会覆盖目标数据库和日志卷，执行前必须确认备份来源和目标环境。

## 9. 升级与回滚

升级前先执行备份并确认没有关键执行任务：

```bash
cd /opt/opspilot/deploy
bash scripts/backup.sh
docker compose build
docker compose up -d
docker compose ps
```

API 重启时会自动执行增量迁移。升级后重新检查 `/healthz`、`/api/v1/ping` 和一条测试工单链路。

回滚需要同时恢复与旧代码兼容的业务镜像和数据库备份；如果升级包含数据库结构迁移，只回退镜像不足以保证兼容。不要删除数据卷作为常规回滚手段。

## 10. HTTPS

当前 Compose 和 Nginx 默认只监听 HTTP。生产环境可在宿主机或前置网关终止 TLS，再将流量转发到 Nginx；前端使用相对 `/api` 路径，不需要修改前端变量。若直接改 Compose 增加 443 端口和证书挂载，必须同步修改 `nginx.conf` 的 `listen`、证书路径和 HTTP 跳转规则，并在变更后运行 `docker compose up -d nginx` 验证。

## 11. 常见故障

| 现象 | 检查 |
| --- | --- |
| API 重启 | `docker logs opspilot-api`；检查 MySQL/Redis health、迁移和 `.env` |
| MySQL 不健康 | 检查初始化日志、磁盘和首次建卷密码；修改 `.env` 不会改变已初始化数据卷密码 |
| Worker 未启动 | 确认 API healthy，再查看 `docker logs opspilot-worker` |
| 页面 502 | `curl http://localhost:8080/healthz`；API 未健康时 Nginx 无法反代 |
| 工单不执行 | 检查 Worker、Redis `redis-cli ping`、全局并发和作业主机连通性 |
| 日志不刷新 | 检查 `/events` 长轮询和 `/logs` 请求、Nginx/前置代理读取超时 |
| 通知失败 | 检查通知渠道启用状态、事件映射、Worker 日志和出站网络；记录会按退避重试 |
| SSH 全部失败 | 检查作业主机 IP/端口、绑定凭据、工作目录和防火墙 |
| 磁盘增长 | 检查 `mysql-data`、`exec-logs` 和 Docker 镜像；按备份策略归档日志 |

提交问题时附带 `docker compose ps` 以及相关容器最近 200 行日志。
