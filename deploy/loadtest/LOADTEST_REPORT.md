# M8-2 压测实测报告

> 实测时间：2026-07-29（容器内时钟）；环境：单机 Docker Compose（五业务容器 + 压测三容器）。
> 压测设施与复现步骤见 [docker-compose.loadtest.yml](docker-compose.loadtest.yml) 头部注释。

## 一、500 台单工单执行压测（Shell + Ansible 两步）

- 拓扑：1 个 sshd 矩阵容器挂 500 个 secondary IP（172.28.4.1~172.28.5.250），
  兼作 Ansible 作业主机（172.28.0.10）；`opspilot-api` / `opspilot-worker` 接入压测网。
- 数据：Excel 一次性导入 1000 台（`success_count=1000, failed_rows=[]`），
  应用 `lt-app-500` 关联其中 500 台真实可连主机。
- 模板：`LT-压测-Shell+Ansible`，步骤 1 Shell（echo+sleep 1）、步骤 2 Ansible ping；
  执行策略并发 50（顶到全局信号量上限）、不分批、失败不中断。

| 项目 | 结果 |
| --- | --- |
| 工单 | T20260729-0001（提交 → admin 一级审批 → 自动入队） |
| 最终状态 | success |
| 步骤 1 Shell 回显 | 500 成功 / 0 失败 |
| 步骤 2 Ansible ping | 500 成功 / 0 失败 |
| 端到端耗时（审批通过 → 终态） | 65.1s |

通知落端点验证（冲突声明 C1：以模拟端点实际收到为准）：

| 渠道 | 端点 | 收到 |
| --- | --- | --- |
| email | mailpit（SMTP :1025） | 3 封（pending_approval / approved / execution.success） |
| webhook | webhook-echo `/hook` | 3 条，含 `{"event": "execution.success", ...}` |
| teams | webhook-echo `/teams` | 3 条，MessageCard 格式 |

## 二、列表接口 P95（验收线 P95 < 500ms，PRD §5）

方法：api 容器内 httpx，10 并发协程 / 每接口 200 请求，预热一轮，最近秩法百分位。
数据规模：主机 1000 行、审计日志 190+ 行（分区表）、其余为真实业务数据。

| 接口 | P50 | P95 | P99 | MAX | 结论 |
| --- | ---: | ---: | ---: | ---: | :---: |
| 主机列表（千行库） | 27.1ms | 40.2ms | 52.0ms | 57.0ms | PASS |
| 主机列表+keyword | 28.8ms | 34.4ms | 37.0ms | 39.7ms | PASS |
| 主机深分页（page=25） | 26.6ms | 31.7ms | 33.6ms | 36.9ms | PASS |
| 应用列表 | 51.3ms | 68.5ms | 143.8ms | 144.9ms | PASS |
| 用户列表 | 30.7ms | 35.3ms | 36.6ms | 39.0ms | PASS |
| 模板列表 | 24.0ms | 29.5ms | 32.6ms | 33.2ms | PASS |
| 工单列表 | 32.3ms | 44.4ms | 48.8ms | 52.5ms | PASS |
| 执行列表 | 31.3ms | 44.8ms | 52.7ms | 54.8ms | PASS |
| 审计日志（分区表） | 27.7ms | 34.9ms | 37.5ms | 38.3ms | PASS |
| 审计+组合筛选 | 30.8ms | 34.9ms | 37.0ms | 37.2ms | PASS |

**结论：10/10 接口 P95 全部 < 500ms（最高 68.5ms），验收通过。**

## 三、复现命令

```bash
# 1. 启动压测设施并接入业务网
docker compose -p opspilot-loadtest -f deploy/loadtest/docker-compose.loadtest.yml up -d --build
docker network connect opspilot-loadtest_loadtest opspilot-api
docker network connect opspilot-loadtest_loadtest opspilot-worker

# 2. 种子 → 执行压测 → P95（均在 api 容器内跑）
docker cp deploy/loadtest/seed_loadtest.py opspilot-api:/tmp/
docker exec -e PYTHONPATH=/app -w /app opspilot-api python /tmp/seed_loadtest.py
docker cp deploy/loadtest/run_loadtest.py opspilot-api:/tmp/
docker exec -e PYTHONPATH=/app -w /app opspilot-api python /tmp/run_loadtest.py
docker cp deploy/loadtest/loadtest_p95.py opspilot-api:/tmp/
docker exec -e PYTHONPATH=/app -w /app opspilot-api python /tmp/loadtest_p95.py
```
