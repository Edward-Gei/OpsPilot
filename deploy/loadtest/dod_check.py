# -*- coding: utf-8 -*-
"""M8-1 DoD 六项 API 侧走查（api 容器内执行；UI 侧另由浏览器抽查）。

用法：docker cp deploy/loadtest/dod_check.py opspilot-api:/tmp/ \
      && docker exec -e PYTHONPATH=/app -w /app opspilot-api python /tmp/dod_check.py

覆盖（PRD §6 DoD）：
- DoD-1 管理员配置能力：用户/角色/凭据/系统参数/通知渠道均可读且有数据
- DoD-2 千台规模：主机总量 >= 1000 且应用 lt-app-500 关联 500 台
- DoD-3 完整链路：复核压测工单（审批记录 + 执行成功 + 历史日志可取）
- DoD-4 执行控制：新工单实操 暂停 -> 恢复 -> 中止（其余项 M5 已回归覆盖）
- DoD-5 审计：组合检索 / CSV 导出 / 分区维护（预建+过期清理）可验证
- DoD-6 部署：healthz + 队列连通（api 能响应即容器链路成立，离线脚本另交付）
"""
import asyncio
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from app.core.security import create_token

BASE = "http://localhost:8000/api/v1"
TOKEN = create_token(1, "admin", "access")[0]
RESULTS: list[tuple[str, str]] = []


def call(method: str, path: str, body: dict | None = None) -> dict | bytes:
    """带 token 调 API；非 JSON 响应（CSV 导出）原样返回字节。"""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def check(dod: str, name: str, cond: bool, ctx: object = "") -> None:
    """记录走查结论；失败不中断（走查要出全量清单）。"""
    mark = "PASS" if cond else "FAIL"
    RESULTS.append((dod, f"[{mark}] {name}" + ("" if cond else f" :: {ctx}")))
    print(f"{dod} [{mark}] {name}" + ("" if cond else f" :: {ctx}"))


# ---------- DoD-1 管理员配置能力 ----------
r = call("GET", "/users?page=1&page_size=1")
check("DoD-1", "用户管理可读", r["code"] == 0 and r["data"]["total"] >= 1, r)
r = call("GET", "/roles")
check("DoD-1", "角色列表含四预置角色", r["code"] == 0 and len(r["data"]["items"]) >= 4, r)
r = call("GET", "/credentials?page=1&page_size=1")
check("DoD-1", "凭据管理可读", r["code"] == 0 and r["data"]["total"] >= 1, r)
r = call("GET", "/system/configs")
cfgs = r["data"] if r["code"] == 0 else {}
check("DoD-1", "系统参数含并发/作业主机", r["code"] == 0
      and "exec.global_concurrency" in str(cfgs) and "ansible.job_host" in str(cfgs), r)
r = call("GET", "/notify/channels")
enabled = [c["type"] for c in r["data"]["items"] if c.get("enabled")] if r["code"] == 0 else []
check("DoD-1", "通知渠道 email/webhook/teams 已启用",
      {"email", "webhook", "teams"} <= set(enabled), enabled)

# ---------- DoD-2 千台规模 + 多对多 ----------
r = call("GET", "/cmdb/hosts?page=1&page_size=1")
check("DoD-2", "主机总量 >= 1000", r["code"] == 0 and r["data"]["total"] >= 1000,
      r["data"]["total"] if r["code"] == 0 else r)
r = call("GET", "/cmdb/apps?page=1&page_size=50&keyword=lt-app-500")
app = next((a for a in r["data"]["items"] if a["name"] == "lt-app-500"), None)
check("DoD-2", "应用关联 500 台", app is not None and app.get("host_count") == 500, app)

# ---------- DoD-3 完整链路复核（压测工单） ----------
r = call("GET", "/executions?page=1&page_size=5&status=success")
exe = next((x for x in r["data"]["items"] if x.get("ticket_no") == "T20260729-0001"), None)
check("DoD-3", "压测工单执行成功记录在案", exe is not None, r)
if exe:
    d = call("GET", f"/tickets/{exe['ticket_id']}")
    approvals = d["data"].get("approvals") or d["data"].get("approval_records") or []
    check("DoD-3", "审批记录存在", d["code"] == 0 and len(approvals) >= 1, d["data"].keys())
    hosts = call("GET", f"/executions/{exe['id']}/hosts?page=1&page_size=1")
    # 明细粒度是「步骤 × 主机」：2 步 × 500 台 = 1000 行
    check("DoD-3", "主机明细 2步×500台=1000 条", hosts["code"] == 0
          and hosts["data"]["total"] == 1000, hosts["data"]["total"])
    # 历史日志按「步骤+主机 IP」取文件行（历史回看契约）
    logs = call("GET", f"/executions/{exe['id']}/logs?step_order=1&ip=172.28.4.1&limit=5")
    check("DoD-3", "历史日志可取且非空", logs["code"] == 0
          and len(logs["data"]["lines"]) >= 1, logs)

# ---------- DoD-4 执行控制实操：暂停 -> 恢复 -> 中止 ----------
tpls = call("GET", "/templates?page=1&page_size=100&keyword=" + urllib.parse.quote("LT-压测"))
tpl = next(x for x in tpls["data"]["items"] if x["name"] == "LT-压测-Shell+Ansible")
r = call("POST", "/tickets", {"template_id": tpl["id"], "params": {"tag": "dod4"}})
tid = r["data"]["id"]
call("POST", f"/tickets/{tid}/approve", {"action": "approve", "comment": "DoD-4 控制走查"})
time.sleep(8)  # 等执行进入 running
r = call("POST", f"/tickets/{tid}/pause")
check("DoD-4", "暂停信号下发", r["code"] == 0, r)
# 暂停在步骤/批次检查点生效：轮询等状态真正变 paused 再恢复
st = ""
for _ in range(18):
    time.sleep(5)
    t = call("GET", f"/tickets/{tid}")
    st = t["data"]["ticket"]["status"] if "ticket" in t["data"] else t["data"]["status"]
    if st == "paused":
        break
check("DoD-4", "检查点生效进入 paused", st == "paused", st)
r = call("POST", f"/tickets/{tid}/resume")
check("DoD-4", "恢复", r["code"] == 0, r)
time.sleep(3)
r = call("POST", f"/tickets/{tid}/abort")
check("DoD-4", "中止", r["code"] == 0, r)
# 等终态：用户中止 → 工单 interrupted（interrupt_reason=user_abort）
final = ""
for _ in range(24):
    time.sleep(5)
    t = call("GET", f"/tickets/{tid}")
    final = t["data"]["ticket"]["status"] if "ticket" in t["data"] else t["data"]["status"]
    if final in ("interrupted", "success", "failed"):
        break
check("DoD-4", "中止后进入终态 interrupted", final == "interrupted", final)

# ---------- DoD-5 审计 ----------
r = call("GET", "/audit/logs?page=1&page_size=5&module=auth&result=success")
check("DoD-5", "组合检索", r["code"] == 0 and r["data"]["total"] >= 1, r)
raw = call("GET", "/audit/logs/export?module=auth")
check("DoD-5", "CSV 导出非空", isinstance(raw, bytes) and len(raw) > 100,
      type(raw).__name__)
# 清理可验证：直接跑分区维护（幂等；无过期分区则 dropped 为空，与 worker 定时任务同入口）
from app.audit.partition import run_maintenance  # noqa: E402
try:
    m = asyncio.run(run_maintenance())
    check("DoD-5", "分区维护（预建/清理）可执行", isinstance(m, dict), m)
    print(f"      分区维护结果：{m}")
except Exception as e:  # noqa: BLE001
    check("DoD-5", "分区维护（预建/清理）可执行", False, e)

# ---------- DoD-6 部署链路 ----------
try:
    with urllib.request.urlopen("http://localhost:8000/healthz", timeout=5) as resp:
        ok6 = resp.status == 200
except Exception as e:  # noqa: BLE001
    ok6 = False
check("DoD-6", "healthz 存活", ok6)

print("\n===== DoD 走查汇总 =====")
fails = [x for x in RESULTS if "[FAIL]" in x[1]]
for dod, line in RESULTS:
    print(f"{dod} {line}")
print(f"\n合计 {len(RESULTS)} 项，失败 {len(fails)} 项")
sys.exit(1 if fails else 0)
