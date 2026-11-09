# OpsPilot 部署手册

适用形态：单机 Docker Compose，在线或纯内网部署。本文只描述当前 `deploy/` 文件可执行的方式；部署变量以 [`.env.example`](.env.example) 为准。

## 1. 组件和数据卷

| 服务 | 作用 | 宿主机端口 |
| --- | --- | --- |
| `opspilot-nginx` | Vue 静态资源、`/api/` 反代、健康检查 | ${NGINX_HTTP_PORT:-8080} |
| `opspilot-api` | FastAPI、迁移、业务 API | 不暴露 |
| `opspilot-worker` | Redis 队列消费、SSH 执行、通知和定时任务 | 不暴露 |
| `database-executor` | 独立数据库任务执行、共享配额、恢复和短期结果清理；容器名由 Compose 为副本生成 | 不暴露 |
| `opspilot-mysql` | MySQL 8 业务数据库 | 不暴露 |
| `opspilot-redis` | Redis 7 队列和缓存，AOF | 不暴露 |

Compose 使用三个命名卷：`mysql-data` 保存数据库（含数据库管理台账/任务/结果/独立审计），`exec-logs` 保存工单执行日志，`redis-data` 保存 Redis AOF。执行器不挂工单日志卷。只有 Nginx 对外提供入口。启动顺序为 MySQL/Redis 健康 -> API（迁移和种子）健康 -> Worker/数据库执行器/Nginx。

## 2. 前置条件

- Docker Engine 24+，且 `docker compose version` 可用。
- 在线构建需要访问 Docker 镜像和依赖源；离线部署按第 5 节导入镜像。
- 部署机磁盘至少 40 GB，执行日志量较大时预留更多空间。
- Worker 到作业主机需要 SSH 出站；通知按渠道需要 SMTP、Webhook 或 Teams 出站。
- 数据库执行器须能访问台账登记的统一主机名/IP和端口；云代理、TLS和取消所需权限单独按第12节验证。
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
| `MFA_TOKEN_MINUTES` / `PWD_CHANGE_TOKEN_MINUTES` | MFA 挑战和强制改密令牌有效期，默认 `5`/`10` 分钟 |
| `RESET_TOKEN_TTL_MINUTES` | 密码重置令牌有效期，默认 `30` 分钟 |
| `PUBLIC_BASE_URL` | 密码重置邮件使用的前端公共地址 |
| `ADMIN_INITIAL_PASSWORD` | 初始 admin 密码；留空则随机生成并写入 API 日志 |
| `AUDIT_RETENTION_DAYS` | 审计数据保留天数，默认 `365` |
| `EXEC_LOG_DIR` | API/Worker 共享的执行日志目录，默认 `/data/logs` |
| `EXEC_GLOBAL_CONCURRENCY_DEFAULT` | 配置项默认 `50`，当前 Worker 未用它限流；不能视为生效的并发上限 |
| `CORS_ORIGINS` | 本地 Vite 直连 API 时的允许来源，生产同源部署通常无需修改 |
| `DEBUG` | 调试开关，生产必须为 `false` |

正式 Access Token 固定 15 分钟；系统设置中的“最长登录时长”默认 8 小时，仅对新登录生效。旧版 `ACCESS_TOKEN_MINUTES`、`REFRESH_TOKEN_DAYS` 和数据库中的对应自定义值不再生效。升级后旧正式令牌因缺少会话截止字段，在首次校验时要求重新登录。

`exec.global_concurrency` 的种子值来自 [SYSTEM_CONFIG_DEFAULTS](../backend/app/core/constants.py)，[种子服务](../backend/app/services/seed.py)只补缺失键；当前执行链未消费该值。已实现的同模板互斥由工单模板的并发控制开关决定，不等同于全局限流。

## 4. 在线部署

执行前先核对[迁移限制](../docs/05-开发与验收指南.md#33-迁移与清理)。0022～0024已增加列存在性检查，0026逐表核对数据库管理九表；仍须验证本次升级来源和业务数据，不用生产库试跑或stamp绕过。跨过0010的历史库另需数据迁移方案。

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

导入脚本加载 `opspilot-api`、`opspilot-worker`、`opspilot-nginx`、`opspilot-database-executor`、`mysql:8.0` 和 `redis:7-alpine` 六个镜像，不带 `--build`；离线包必须包含独立执行器镜像。

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
docker compose logs -f database-executor
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

产物为 `backup/<时间戳>/mysql_opspilot.sql` 和 `exec_logs.tar.gz`。脚本仅备份 MySQL 全库及 `exec-logs` 卷，不包含 Redis AOF 或字段加密主密钥；主密钥应另行安全保存。

Redis 除缓存外还保存执行队列、控制信号、事件游标和令牌吊销等临时认证状态，不能全部由 MySQL 重建。丢失 `redis-data` 后，数据库仍为 `queued` 的执行不保证自动补发，认证及控制状态也可能丢失。恢复方案须单独覆盖 Redis 持久化数据，并核对工单状态与认证状态；不要把本脚本称为无损整站备份，或为补队列而盲目重跑可能产生外部副作用的脚本。

恢复前停止新提交和数据库任务认领，核实活动/恢复中的目标操作，再停止 API/Worker/数据库执行器；确认目标环境后执行以下数据库与日志恢复步骤：

```bash
docker exec -i opspilot-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' < backup/<时间戳>/mysql_opspilot.sql
docker run --rm -v opspilot_exec-logs:/data -v /绝对路径/backup/<时间戳>:/backup alpine tar xzf /backup/exec_logs.tar.gz -C /data
docker compose restart api worker database-executor
```

恢复命令会覆盖目标数据库和日志卷，执行前必须确认备份来源和目标环境。

## 9. 升级与回滚

升级前先核对当前 Alembic 版本与待执行迁移。`0010` 是删除并重建模板、工单、审批等表的历史迁移，不是无损增量升级；需要跨过该版本的已有库必须先制定数据迁移与恢复方案。0022～0024已修复新库重复加列，0026新增数据库管理九表；空库全链与旧结构升级仍分别验证，详见[迁移限制](../docs/05-开发与验收指南.md#33-迁移与清理)。

在隔离副本确认升级可行后，备份并确认没有关键执行任务，再执行：

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
| 数据库任务不执行 | 检查 `docker compose ps database-executor`、执行器日志/心跳、实例启用和显式角色授权、队列与配额；recovering 不应手工清零或自动重跑 |
| 页面 502 | `curl http://localhost:8080/healthz`；API 未健康时 Nginx 无法反代 |
| 工单不执行 | 检查审批状态、Worker、Redis 队列与数据库执行状态、模板互斥和作业主机连通性；不要假定所有 `queued` 都有自动补发 |
| 日志不刷新 | 检查 `/events` 长轮询和 `/logs` 请求、Nginx/前置代理读取超时 |
| 通知失败 | 检查通知渠道启用状态、事件映射、Worker 日志和出站网络；网络类失败按退避重试 |
| SSH 全部失败 | 检查作业主机 IP/端口、绑定凭据、防火墙；Playbook 另检查工作目录与 ansible-playbook |
| 磁盘增长 | 检查 `mysql-data`、`exec-logs` 和 Docker 镜像；按备份策略归档日志 |

提交问题时附带 `docker compose ps` 以及相关容器最近 200 行日志。

## 12. 数据库管理运行与目标权限

执行器与API使用同一后端源码/加密材料，独立启动 `python -m app.database_executor_main`，RUN_MIGRATIONS=0，只有API执行迁移和种子。默认每副本CPU1、内存1GiB、PID256、30秒停止窗口，内部MySQL池4/overflow0；健康检查核对本进程心跳和平台库，不连接目标做健康探测。生产资源值见当前Compose，扩容前确认内部/目标连接和磁盘预算。

在deploy目录扩容与检查：

```bash
docker compose config --quiet
docker compose up -d --build --scale database-executor=2
docker compose ps database-executor
docker compose logs --tail 100 database-executor
```

不要为副本增加固定container_name或公开端口。副本共享全局/实例/用户/队列及密文预算，不把并发上限乘副本数。默认全局20、实例5、用户2是工程起点；Mongo每任务/控制计量包含监控socket，任务槽不是实际连接数。容量策略在系统设置database.*中持久化，由system:config控制，完整默认和范围见[API配置](../docs/04-API设计.md#146-系统配置合同)，无需新增查询密码环境变量。

先在凭据管理登记username_password只读账号，再建实例、选应用/承载主机并给角色显式实例授权。维护权限仅可probe；查询和结构读取需query+grant。实例登记/删除仅改平台记录，删除需先停用且无排队/运行/恢复任务。结果最多1000条/5MiB、30分钟，历史30天、独立审计90天，分别配置；独立审计不提供结果集或导出。

| 目标 | 账号及运行前提 |
| --- | --- |
| MySQL | 可见业务库SELECT/SHOW VIEW；使用只读事务、server_uuid及本人连接/标记核实；账号须能查询自身状态并取消自身查询 |
| PostgreSQL | 目标库CONNECT、Schema USAGE、表SELECT；平台额外建立只读事务/有界游标；取消核实读取自身pg_stat_activity并pg_cancel_backend本人查询 |
| MongoDB | 业务库read和自身操作核实/取消能力；固定directConnection单入口、禁自动发现；必要currentOp/killOp能力按真实版本验证 |
| Redis | 业务键范围只读ACL或受限password-only；固定PING、INFO、CLIENT ID/SETNAME/LIST/KILL用于探测和自身取消核实。这些能力只由适配器使用，不开放为任意命令入口 |
| ClickHouse | 业务库SELECT/结构读取；readonly=2允许平台设置资源限制但仍禁止写入；可信取消需KILL QUERY及SELECT(query_id,user,query) ON system.processes；历史确认需SELECT(query_id,user,type,exception_code,event_time_microseconds) ON system.query_log |

ClickHouse固定max_memory_usage=268435456（256MiB）、max_threads=2，另有限时/结果/块限制，用户不能通过SETTINGS覆盖。KILL返回包含query列，因此system.processes的query列是实际前提；平台不向UI或独立审计返回KILL正文，但该目标账号具有该列可见性，正式部署须评估账号权限范围。不能删掉必要列后仍宣称可取消。

ClickHouse只接受精确query_id+user的KILL SYNC finished或query_log正向终态证据，日志禁用/延迟、代理命中其他节点或权限不足时保持recovering；其他引擎同样先比可信实例与操作身份。close本地连接、HTTP超时、零匹配或租约到期都不等于目标已结束。控制/关闭状态未知时保留占用与审计，不强改成功、不自动重跑、不通过清空quota恢复容量。

当前真实目标验证使用MySQL8/PostgreSQL16/MongoDB7/Redis7/ClickHouse24.8直连非TLS容器。云代理、SQL事务亲和性、TLS证书/自签证书、其他版本和实际控制权限仍需在登记入口认证后验证；本地端口或probe通过不能代替完整读取/时限/取消验收。协议和执行器RSS护栏不代表目标服务器任意函数/视图成本有统一上限。

数据库管理验收应在独立环境使用本目录 Compose，准备独立数据卷和五类目标数据库的受限账号，不复用生产凭据或历史数据卷。启动、定向测试、迁移和容量检查见[开发验收](../docs/05-开发与验收指南.md#52-运行与检查入口)；正式部署仍按第4节配置密钥和管理员账号。

停机/回退先停新提交和认领，再核实活动/恢复目标的可信结束；30秒容器停止窗口不能保证远端所有操作结束。备份MySQL全库及加密材料，结果/任务/审计随库保存；恢复不重放旧running任务。镜像回退不直接执行0026 downgrade（会删除九表），也不删除卷；未知控制连接的保守保留可能降低容量，应按目标证据处置。
