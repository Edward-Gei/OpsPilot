# -*- coding: utf-8 -*-
"""凭据关联与脚本调用凭据 部署冒烟（在 api 容器内执行）。

链路：凭据 → 作业主机关联凭据 → 连通性测试 → 模板 credential_refs →
免审提单执行 → 日志可见 CRED_* 环境变量注入、快照/日志无密文。

用法：docker cp deploy/smoke_credential.py opspilot-api:/tmp/
      docker exec opspilot-api python /tmp/smoke_credential.py <目标机IP> <root密码>
"""
import json
import sys
import time
import urllib.error
import urllib.request

from app.core.security import create_token

BASE = "http://localhost:8000/api/v1"
TOKEN = create_token(1, "admin", "access")[0]
TARGET_IP = sys.argv[1] if len(sys.argv) > 1 else "172.18.0.7"
TARGET_PWD = sys.argv[2] if len(sys.argv) > 2 else "Smoke123!"
PASS = []


def call(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def check(name: str, cond: bool, ctx: object = "") -> None:
    if not cond:
        print(f"[FAIL] {name} :: {ctx}")
        sys.exit(1)
    PASS.append(name)
    print(f"[ok] {name}")


ts = int(time.time())

# 1. 创建凭据（真实可登录目标机）
r = call("POST", "/credentials", {
    "name": f"smoke-cred-{ts}", "login_user": "root",
    "auth_type": "password", "secret": TARGET_PWD,
})
check("POST /credentials", r["code"] == 0, r)
cred_id = r["data"]["id"]

# 2. 作业主机关联凭据（不填任何账号/密文；可重跑：已存在则复用并切换凭据）
r = call("POST", "/job-hosts", {
    "name": f"smoke-jh-{ts}", "ip": TARGET_IP, "ssh_port": 22,
    "credential_id": cred_id, "workdir": "/tmp/opspilot-smoke",
})
if r["code"] == 42201:  # ip:port 唯一键冲突 → 复用存量主机
    items = call("GET", "/job-hosts?page=1&page_size=100")["data"]["items"]
    jh_id = next(h["id"] for h in items if h["ip"] == TARGET_IP)
    r = call("PUT", f"/job-hosts/{jh_id}", {"credential_id": cred_id})
    check("PUT /job-hosts（切换关联凭据）", r["code"] == 0, r)
else:
    check("POST /job-hosts（credential_id 关联）", r["code"] == 0, r)
    jh_id = r["data"]["id"]
d = call("GET", f"/job-hosts/{jh_id}")
check("详情带 credential_name 无密文",
      d["data"].get("credential_name") == f"smoke-cred-{ts}"
      and "secret_enc" not in d["data"] and "auth_type" not in d["data"], d)

# 3. 连通性测试：以关联凭据实时建连
r = call("POST", f"/job-hosts/{jh_id}/test")
check("POST /job-hosts/{id}/test（凭据建连）", r["code"] == 0 and r["data"].get("ok"), r)

# 4. 被引用凭据删除保护
r = call("DELETE", f"/credentials/{cred_id}")
check("被作业主机引用的凭据删除 → 42201", r["code"] == 42201, r)

# 5. 模板声明引用凭据（免审，脚本读取 CRED_MYSQL_* 环境变量）
tpl_body = {
    "name": f"smoke-凭据注入-{ts}", "type": "daily_ops", "job_host_id": jh_id,
    "steps": [{
        "name": "读取凭据环境变量", "script_type": "shell",
        # 注意：避免 ${#VAR} 写法（{# 会被 Jinja2 识别为注释起始符）
        "content": 'echo "user=$CRED_MYSQL_USER"; if [ -n "$CRED_MYSQL_SECRET" ]; then echo "secret=set"; fi',
        "params_schema": [], "timeout": 60,
    }],
    "approval_enabled": False,
    "credential_refs": [{"alias": "mysql", "credential_id": cred_id}],
}
r = call("POST", "/templates", tpl_body)
check("POST /templates（credential_refs）", r["code"] == 0, r)
tpl_id = r["data"]["id"]
d = call("GET", f"/templates/{tpl_id}")
check("模板详情回显 alias+credential_name",
      d["data"]["credential_refs"][0]["alias"] == "mysql"
      and d["data"]["credential_refs"][0]["credential_name"] == f"smoke-cred-{ts}", d)

# 6. 提单（免审直接入队执行）
r = call("POST", "/tickets", {"template_id": tpl_id, "params": {}})
check("POST /tickets（免审入队）", r["code"] == 0 and r["data"]["status"] in ("queued", "running"), r)
tid = r["data"]["id"]

# 7. 轮询至终态
detail = None
for _ in range(60):
    detail = call("GET", f"/tickets/{tid}")["data"]
    if detail["status"] in ("success", "failed", "interrupted"):
        break
    time.sleep(2)
check("工单终态 success", detail is not None and detail["status"] == "success", detail and detail["status"])
check("工单快照冻结 credential_refs（含 credential_name）",
      detail["credential_refs"][0]["alias"] == "mysql"
      and detail["credential_refs"][0]["credential_name"] == f"smoke-cred-{ts}", detail.get("credential_refs"))
check("工单详情无密文", TARGET_PWD not in json.dumps(detail, ensure_ascii=False), "")

# 8. 执行日志：env 注入生效且不含密文
eid = detail["execution"]["id"]
logs = call("GET", f"/executions/{eid}/logs?step_order=1&offset=0")
text = "\n".join(line["line"] if isinstance(line, dict) else str(line) for line in logs["data"]["lines"])
print("---- step1 log ----")
print(text)
print("-------------------")
check("日志可见 user=root（CRED_MYSQL_USER 注入）", "user=root" in text, text)
check("日志可见 secret=set（CRED_MYSQL_SECRET 注入）", "secret=set" in text, text)
check("日志不含密文明文", TARGET_PWD not in text, "")

# 9. 收尾：禁用模板（保留工单留痕）
r = call("PUT", f"/templates/{tpl_id}/status", {"status": "disabled"})
check("PUT status disabled", r["code"] == 0, r)

print(f"\nSMOKE PASSED: {len(PASS)} checks")
