# OpsPilot V1 生产环境部署手册

> **文档定位**：面向生产/内网环境的完整部署与运维手册（Day-0 部署 → Day-1 初始化 → Day-2 运维）。
> 快速上手概要见根目录 [README.md](../README.md)；本文档所有命令与配置均与 `deploy/` 下实际文件核对一致。
>
> **适用版本**：V1 竣工版 ｜ **部署形态**：单机 Docker Compose ｜ **更新日期**：2026-07

## 目录

1. [系统架构与组件清单](#一系统架构与组件清单)
2. [部署前置条件](#二部署前置条件)
3. [安全基线（生产必改项）](#三安全基线生产必改项)
4. [标准部署（在线环境）](#四标准部署在线环境)
5. [离线部署（纯内网）](#五离线部署纯内网)
6. [部署验证](#六部署验证)
7. [首次初始化](#七首次初始化)
8. [HTTPS/TLS 启用指引](#八httpstls-启用指引)
9. [日常运维（Day-2）](#九日常运维day-2)
10. [升级与回滚](#十升级与回滚)
11. [故障排查](#十一故障排查)
12. [附录：.env 配置全量清单](#十二附录env-配置全量清单)

---

## 一、系统架构与组件清单

### 1.1 容器拓扑

```
浏览器 ──HTTP :8080──> nginx ──/api、/ws、/healthz──> api (uvicorn :8000)
                        │静态资源(Vue3 构建产物)        │
                                                       ├──> mysql :3306（compose 内网）
                                                       └──> redis :6379（compose 内网）
                                          worker（执行引擎）──┘ ──SSH──> 目标主机
```

| 容器名 | 镜像 | 职责 | 对宿主机暴露端口 |
| --- | --- | --- | --- |
| `opspilot-nginx` | `frontend/` 构建（nginx:1.27-alpine） | 静态资源 + `/api` 反向代理 | `${NGINX_HTTP_PORT:-8080}` → 80 |
| `opspilot-api` | `backend/` 构建（python:3.11-slim） | REST API（含实时长轮询）+ Alembic 迁移 | 无（仅 compose 内网） |
| `opspilot-worker` | 与 api 共用镜像，入口 `app.worker_main` | 工单执行引擎（SSH/Ansible）、通知投递 | 无 |
| `opspilot-mysql` | mysql:8.0 | 业务数据库（utf8mb4 全局） | 无 |
| `opspilot-redis` | redis:7-alpine（AOF 开启） | 执行队列 / 缓存 | 无 |

> **安全要点**：仅 nginx 一个端口对外，mysql/redis/api 均不映射宿主机端口，外部无法直连。

### 1.2 数据卷（数据都在这里，务必纳入备份）

| 卷名（实际名带 `opspilot_` 前缀） | 挂载点 | 内容 | 备份方式 |
| --- | --- | --- | --- |
| `opspilot_mysql-data` | mysql:/var/lib/mysql | 全部业务数据 | `mysqldump` 逻辑备份（见 §9.1） |
| `opspilot_exec-logs` | api、worker:/data/logs | 执行历史落盘日志 | tar 归档（见 §9.1） |
| `opspilot_redis-data` | redis:/data | 队列/缓存（AOF） | 无需备份（可重建） |

### 1.3 启动依赖顺序（compose 自动编排）

```
mysql(healthy) + redis(healthy) → api(healthy，启动时执行 Alembic 迁移) → worker、nginx
```

- 仅 api 容器执行数据库迁移（`RUN_MIGRATIONS=1`）；worker 等待 api 健康后启动，天然避免并发迁移。
- 所有容器 `restart: unless-stopped`，宿主机重启后自动拉起。

---

## 二、部署前置条件

### 2.1 硬件与操作系统

| 项目 | 最低要求 | 推荐配置 |
| --- | --- | --- |
| CPU | 2 核 | 4 核（500 台并发执行场景） |
| 内存 | 4 GB | 8 GB |
| 磁盘 | 40 GB（含镜像与数据卷） | 100 GB SSD（视执行日志/审计量增长） |
| 操作系统 | 任意可运行 Docker 的 Linux x86_64（如 CentOS 7.9+ / Ubuntu 20.04+ / 银河麒麟等） | — |

### 2.2 软件依赖

- **Docker Engine 24+（含 compose v2 插件）**——唯一硬依赖，部署机无需安装 Python/Node/MySQL。
- 验证：`docker version`、`docker compose version` 均能正常输出。

### 2.3 网络与端口

| 方向 | 端口 | 说明 |
| --- | --- | --- |
| 入站 | `NGINX_HTTP_PORT`（默认 8080，启用 HTTPS 后另加 443） | 用户访问入口，需防火墙放行 |
| 出站 | 22（或自定义 SSH 端口） | worker → 目标主机执行 |
| 出站 | 25/465/587（按邮件服务器而定） | 邮件通知 |
| 出站 | 443/80 | Webhook / Teams 通知端点 |
| 出站 | LDAP 389/636、OIDC 端点 | 仅启用对应 SSO 时需要 |

防火墙放行示例（firewalld）：

```bash
firewall-cmd --permanent --add-port=8080/tcp && firewall-cmd --reload
```

### 2.4 时区

镜像内已固化 `TZ=Asia/Shanghai`（mysql 与后端镜像），无需额外配置；如需其他时区请修改 compose 后重建。

---

## 三、安全基线（生产必改项）

部署前逐项确认，**缺一不可上线**：

| # | 项目 | 操作 |
| :---: | --- | --- |
| 1 | `MYSQL_ROOT_PASSWORD` / `MYSQL_PASSWORD` | 改为强密码（≥16 位混合字符）。**注意**：仅在数据卷首次初始化时生效，上线后改密码见 §11 FAQ |
| 2 | `JWT_SECRET_KEY` | `openssl rand -hex 32` 生成，泄露等于全部会话失守 |
| 3 | `SECRET_ENCRYPT_KEY` | `openssl rand -base64 32` 生成。用于 MFA secret 与 SSH 凭据字段级加密（AES-256-GCM）；留空则从 JWT 密钥派生并在日志告警。**生成后必须离线留存，丢失将无法解密已存凭据** |
| 4 | `DEBUG` | 保持 `false` |
| 5 | `.env` 文件权限 | `chmod 600 deploy/.env`，禁止入版本库（`.gitignore` 已忽略） |
| 6 | `ADMIN_INITIAL_PASSWORD` | 建议留空（随机生成打印到 api 日志，仅首次种子时使用），或设置后首登即改 |
| 7 | 对外协议 | 生产强烈建议启用 HTTPS（见 §8） |
| 8 | 宿主机 | 最小化安装、及时打补丁；Docker 仅监听本地 socket |

> 纵深防御现状：后端容器以非 root 用户（uid 1000）运行；mysql/redis 不对宿主机暴露端口；
> admin 首次登录强制改密 + 绑定 MFA（TOTP）。

---

## 四、标准部署（在线环境）

适用：部署机可访问外网（拉取基础镜像、pip/npm 依赖）。

```bash
# 1. 获取代码（git clone 或解压发布包），进入 deploy 目录
cd /opt/opspilot/deploy        # 以实际路径为准

# 2. 生成并修改配置（对照 §3 安全基线逐项确认）
cp .env.example .env
vi .env                        # 必改：两个 MySQL 密码 + JWT_SECRET_KEY；建议：SECRET_ENCRYPT_KEY
chmod 600 .env

# 3. 构建并启动五容器（首次构建约 5~10 分钟，视网络而定）
docker compose up -d --build

# 4. 观察启动（等待全部 healthy / running）
docker compose ps
docker logs -f opspilot-api    # 关注"执行数据库迁移 alembic upgrade head"成功与初始 admin 密码
```

启动完成后访问 `http://<部署机IP>:8080`。

---

## 五、离线部署（纯内网）

适用：部署机完全无外网。分两步：有外网的**构建机**导出镜像 → 内网**部署机**导入启动。

### 5.1 构建机（有外网）

```bash
cd <代码目录>/deploy
bash scripts/export_images.sh export
# 产物：deploy/offline/opspilot_images.tar.gz（含 5 个镜像：
#   opspilot-api / opspilot-worker / opspilot-nginx / mysql:8.0 / redis:7-alpine）
```

将 **tar 包 + 整个 `deploy/` 目录**（compose、nginx.conf、mysql/init、.env.example、scripts）拷贝到内网部署机。

### 5.2 部署机（内网）

```bash
cd /opt/opspilot/deploy
bash scripts/export_images.sh import   # docker load 导入 5 个镜像

cp .env.example .env
vi .env && chmod 600 .env              # 同 §4 第 2 步

docker compose up -d                   # 注意：不带 --build，直接使用导入镜像
docker compose ps
```

> 离线升级同理：构建机重新 export → 拷贝 → import → `docker compose up -d`（详见 §10.2）。

---

## 六、部署验证

### 6.1 基础探活

```bash
# 五容器全部 Up 且 mysql/api 显示 (healthy)
docker compose ps

# 整链路健康检查（浏览器→nginx→api）：期望返回 200
curl -s http://localhost:8080/healthz

# 公开探活接口：期望返回 code=0
curl -s http://localhost:8080/api/v1/ping
```

### 6.2 冒烟测试（模板→工单→审批全链路）

```bash
# 在 api 容器内执行，复用/创建 smoke-v2-* 前缀资源，结束后禁用模板不删除（保留留痕）
docker cp smoke_v2.py opspilot-api:/tmp/
docker exec -e PYTHONPATH=/app -w /app opspilot-api python /tmp/smoke_v2.py
# 期望：逐项 [ok] 输出，无 [FAIL]
```

### 6.3 后端测试套件（可选，交付验收用）

```bash
docker exec opspilot-api python -m pytest tests/ -q
```

### 6.4 页面验证

浏览器打开 `http://<IP>:8080` → 出现登录页 → admin 登录 → 强制改密 → 绑定 MFA（TOTP，用手机认证器扫码）→ 进入工作台。

---

## 七、首次初始化

### 7.1 获取初始 admin 密码

- `.env` 设置了 `ADMIN_INITIAL_PASSWORD`：直接使用该密码。
- 留空（推荐）：随机密码打印在 api 容器日志——`docker logs opspilot-api | grep 初始`。
- 首次登录强制修改密码并绑定 MFA。

### 7.2 业务初始化顺序（Checklist）

按依赖顺序执行，全部在 Web 页面完成：

- [ ] 1. **系统配置 → 凭据管理**：录入 SSH 凭据（密码/密钥，落库前 AES-256-GCM 加密）
- [ ] 2. **CMDB → 主机管理**：手工录入或 Excel 批量导入（模板页内下载，单次 ≤5000 行）
- [ ] 3. **CMDB → 应用管理**：创建应用并关联主机（多对多）
- [ ] 4. **系统配置 → Ansible 作业主机**：指定作业机 + 凭据并测试连通（Playbook 模板的前置条件）
- [ ] 5. **作业模板**：编排多步 Pipeline（Shell/Playbook）、执行策略（并发/分批/超时/失败策略）、审批节点、通知规则
- [ ] 6. **通知配置**：启用邮件/Webhook/Teams 渠道并发送测试消息验证送达
- [ ] 7. **工单闭环验证**：选模板提交 → 审批 → 实时日志观察执行 → 确认通知送达
- [ ] 8. **用户与角色**：按需创建用户、分配角色（内置 4 角色，20 个权限点可自定义组合）
- [ ] 9. **系统配置核对**：MFA 策略、令牌策略、密码策略、全局执行并发（默认 50）按需调整

---

## 八、HTTPS/TLS 启用指引

当前 `nginx.conf` 仅监听 HTTP。生产启用 HTTPS 的改造步骤（改配置即可，无需重建镜像）：

### 8.1 准备证书

将证书放到 `deploy/certs/`（自建目录）：`server.crt` + `server.key`（PEM 格式；内网可用企业 CA 签发）。

### 8.2 修改 `deploy/docker-compose.yml` 的 nginx 服务

```yaml
  nginx:
    ports:
      - "${NGINX_HTTP_PORT:-8080}:80"
      - "${NGINX_HTTPS_PORT:-8443}:443"        # 新增 HTTPS 端口
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./certs:/etc/nginx/certs:ro            # 新增证书挂载
```

### 8.3 修改 `deploy/nginx.conf`

在现有 `server` 块基础上调整（保留原有 `/`、`/api/`、`/healthz` 三个 location 不变）：

```nginx
# HTTP 仅做跳转
server {
    listen 80;
    server_name _;
    return 301 https://$host:8443$request_uri;   # 端口按实际对外端口调整
}

server {
    listen 443 ssl;
    server_name _;

    ssl_certificate     /etc/nginx/certs/server.crt;
    ssl_certificate_key /etc/nginx/certs/server.key;
    ssl_protocols       TLSv1.2 TLSv1.3;

    # ……以下原样保留现有配置：root/index/client_max_body_size/gzip
    # 及 location /、/api/、/healthz 三段（实时日志走 /api 长轮询，无需额外配置）
}
```

### 8.4 生效

```bash
docker compose up -d nginx     # 仅重建 nginx 容器
curl -sk https://localhost:8443/healthz
```

> 前端通过相对路径访问 `/api` 与 `/ws`，切换 HTTPS 后自动升级为 `https/wss`，无需改前端配置。

---

## 九、日常运维（Day-2）

### 9.1 备份（建议每日 crontab）

```bash
bash deploy/scripts/backup.sh
# 产物：deploy/backup/<时间戳>/
#   ├── mysql_opspilot.sql   # 全库逻辑备份（--single-transaction 不锁表，含存储过程/触发器）
#   └── exec_logs.tar.gz     # 执行日志卷归档
```

crontab 示例（每日 02:00，产物建议再同步至异地）：

```
0 2 * * * cd /opt/opspilot && bash deploy/scripts/backup.sh >> /var/log/opspilot_backup.log 2>&1
```

> Redis 仅作队列/缓存（AOF 已开启），无需纳入备份。

### 9.2 恢复

```bash
# 1. MySQL 全库恢复（覆盖式，请先确认目标环境）
docker exec -i opspilot-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" opspilot' \
  < deploy/backup/<时间戳>/mysql_opspilot.sql

# 2. 执行日志卷恢复
docker run --rm -v opspilot_exec-logs:/data -v /opt/opspilot/deploy/backup/<时间戳>:/backup alpine \
  tar xzf /backup/exec_logs.tar.gz -C /data

# 3. 重启应用容器使状态一致
docker compose restart api worker
```

### 9.3 常用运维命令

```bash
docker compose ps                          # 容器状态（关注 healthy）
docker logs -f opspilot-api                # API 日志（含迁移、登录、审计写入）
docker logs -f opspilot-worker             # 执行引擎日志（SSH/Ansible/通知投递）
docker logs --tail 200 opspilot-nginx      # 访问日志/入口错误
docker compose restart worker              # 单容器重启（执行中任务的恢复行为见 §11）
docker compose down                        # 全停（数据卷保留）
docker compose up -d                       # 再启动
docker system df                           # 磁盘占用检查
```

### 9.4 例行巡检建议

| 频率 | 项目 |
| --- | --- |
| 每日 | `docker compose ps` 全绿；备份产物生成且大小正常 |
| 每周 | 磁盘余量（mysql-data / exec-logs 卷增长）；`docker logs` 有无异常报错 |
| 每月 | 恢复演练（备份文件在测试环境可成功导入）；审计保留策略生效（默认 365 天自动清理过期分区） |

---

## 十、升级与回滚

### 10.1 升级前准备（必做）

```bash
# 1. 完整备份（数据库 + 日志卷）
bash deploy/scripts/backup.sh

# 2. 给当前业务镜像打回滚标签（基础镜像 mysql/redis 版本不变无需处理）
docker tag opspilot-api:latest    opspilot-api:rollback
docker tag opspilot-worker:latest opspilot-worker:rollback
docker tag opspilot-nginx:latest  opspilot-nginx:rollback

# 3. 选择低峰期，确认无执行中工单（执行管理页面无 running 状态）
```

### 10.2 升级步骤

**在线环境**：

```bash
cd /opt/opspilot
git pull                       # 或解压新版本发布包覆盖代码
cd deploy
docker compose build           # 重建业务镜像
docker compose up -d           # 滚动替换；api 重启时自动执行增量 Alembic 迁移
docker compose ps              # 等待全部 healthy
```

**离线环境**：构建机 `bash scripts/export_images.sh export` → 拷贝新 tar 包 →
部署机 `bash scripts/export_images.sh import` → `docker compose up -d`。

升级后按 §6 执行验证（healthz + 冒烟）。

### 10.3 回滚预案

```bash
# 1. 镜像回退
docker tag opspilot-api:rollback    opspilot-api:latest
docker tag opspilot-worker:rollback opspilot-worker:latest
docker tag opspilot-nginx:rollback  opspilot-nginx:latest
docker compose up -d --no-build

# 2. 若新版本已执行数据库迁移（表结构有变化），旧代码可能不兼容新结构，
#    需同时恢复升级前的数据库备份（§9.2）——这也是升级前备份为必做项的原因。
```

> 判断是否需要恢复数据库：查看升级日志中 api 容器是否输出了新的 `alembic upgrade` 迁移记录；
> 无新迁移则仅回退镜像即可。

---

## 十一、故障排查

| 现象 | 定位命令 | 常见原因与处理 |
| --- | --- | --- |
| api 一直重启 / 启动即退出 | `docker logs opspilot-api` | ① Alembic 迁移失败：看报错修复后 `docker compose restart api`；② 数据库连不上：确认 mysql 已 healthy、`.env` 中 MySQL 账号密码正确 |
| mysql 始终不健康 | `docker logs opspilot-mysql` | 首次初始化磁盘不足/权限问题；或**修改了 `.env` 密码但数据卷已初始化**（环境变量密码仅首次建卷生效）——改密需进容器执行 `ALTER USER`，或确认可清空数据后 `docker compose down -v` 重建 |
| worker 未启动 | `docker compose ps` | worker 依赖 api healthy，先解决 api 问题 |
| 页面打不开（连接拒绝） | `docker logs opspilot-nginx`；`ss -lntp \| grep 8080` | 端口被占用：改 `.env` 的 `NGINX_HTTP_PORT` 后 `docker compose up -d nginx`；防火墙未放行入站端口 |
| 页面能开但接口全 502 | `curl http://localhost:8080/healthz` | api 未 healthy 或崩溃，回到第一行排查 |
| 实时日志不刷新 | 浏览器网络面板（/events、/logs 请求）+ `docker logs opspilot-nginx` | 实时日志为 REST 长轮询（/events 挂起 30s + /logs 每 2s 增量拉取）；若前置企业级 LB/代理，需确认其读超时 ≥ 60s，避免长轮询挂起期间被提前切断 |
| 工单一直排队不执行 | `docker logs -f opspilot-worker` | worker 挂了（compose 会自动拉起）；全局并发被占满（系统配置 `exec.global_concurrency`，默认 50）；redis 异常 `docker exec opspilot-redis redis-cli ping` |
| worker 重启后任务状态 | 执行详情页 | 设计行为：queued 任务自动重认领重跑；重启时 running/paused 的任务标记 interrupted 并记录原因，需人工确认后重新发起 |
| 通知未送达 | `docker logs opspilot-worker`；通知配置页"发送测试" | 渠道未启用/配置错误；部署机到 SMTP/Webhook 端点的出站网络不通（§2.3） |
| Excel 导入失败（413） | 浏览器网络面板 | 文件超过 nginx `client_max_body_size 50m`，拆分文件或调大该值后 `docker compose up -d nginx` |
| 磁盘增长过快 | `docker system df`；`du -sh` 数据卷 | 执行日志卷随任务量增长：结合备份策略定期归档清理；审计日志按 `AUDIT_RETENTION_DAYS`（默认 365）自动清理过期分区 |
| SSH 执行全部失败 | 执行日志 + worker 日志 | 凭据错误/过期；worker 容器到目标主机 22 端口网络不通；目标主机 sshd 限制（MaxSessions/防火墙） |

> 提交问题时请附带：`docker compose ps` 输出 + 相关容器最近 200 行日志（`docker logs --tail 200 <容器名>`）。

---

## 十二、附录：.env 配置全量清单

以 [.env.example](.env.example) 为唯一事实来源，此处汇总说明：

| 变量 | 必改 | 默认值 | 说明 |
| --- | :---: | --- | --- |
| `NGINX_HTTP_PORT` | | 8080 | 对外 HTTP 端口 |
| `MYSQL_ROOT_PASSWORD` | ✔ | — | root 密码，仅数据卷首次初始化时生效 |
| `MYSQL_HOST` / `MYSQL_PORT` | | mysql / 3306 | compose 内网地址，单机部署勿改 |
| `MYSQL_USER` / `MYSQL_PASSWORD` | ✔ | opspilot / — | 业务账号 |
| `MYSQL_DB` | | opspilot | 库名 |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` | | redis / 6379 / 0 | compose 内网，默认无密码（不对外暴露） |
| `JWT_SECRET_KEY` | ✔ | — | JWT 签名密钥，`openssl rand -hex 32` |
| `SECRET_ENCRYPT_KEY` | 建议 | 空 | AES-256-GCM 字段加密主密钥，`openssl rand -base64 32`；留空从 JWT 密钥派生并告警；**丢失即无法解密已存 MFA/SSH 凭据** |
| `ACCESS_TOKEN_MINUTES` | | 15 | 访问令牌时效（分钟） |
| `REFRESH_TOKEN_DAYS` | | 7 | 刷新令牌时效（天） |
| `MFA_TOKEN_MINUTES` | | 5 | MFA 临时令牌时效（分钟） |
| `PWD_CHANGE_TOKEN_MINUTES` | | 10 | 首登改密临时令牌时效（分钟） |
| `ADMIN_INITIAL_PASSWORD` | | 空 | 初始 admin 密码；留空随机生成并打印到 api 日志 |
| `AUDIT_RETENTION_DAYS` | | 365 | 审计日志保留天数，过期分区凌晨自动清理 |
| `EXEC_LOG_DIR` | | /data/logs | 执行日志落盘目录（api/worker 共享卷挂载点，勿改） |
| `EXEC_GLOBAL_CONCURRENCY_DEFAULT` | | 50 | 全局 SSH 并发默认值，仅首次启动写入系统配置，之后以页面配置为准 |
| `CORS_ORIGINS` | | http://localhost:5173 | 仅本地开发 vite 直连时使用，生产同源无需修改 |
| `DEBUG` | | false | 生产必须保持 false |

---

*本手册与 `deploy/` 下实际配置文件保持同步维护；配置变更时请同步更新本文档。*
