# -*- coding: utf-8 -*-
"""V2 部署冒烟：模板 CRUD/升版 → 工单提交/审批/撤回 全链路（在 api 容器内执行）。

用法：docker cp deploy/smoke_v2.py opspilot-api:/tmp/ && docker exec opspilot-api python /tmp/smoke_v2.py
数据策略：复用/创建 smoke-v2-* 前缀资源，结束后禁用模板（不删除，保留工单留痕）。
"""
import json
import sys
import time
import urllib.error
import urllib.request

from app.core.security import create_token

BASE = "http://localhost:8000/api/v1"
TOKEN = create_token(1, "admin", "access")[0]
PASS = []


def call(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def check(name: str, cond: bool, ctx: object = "") -> None:
    if not cond:
        print(f"[FAIL] {name} :: {ctx}")
        sys.exit(1)
    PASS.append(name)
    print(f"[ok] {name}")


# 1. 角色：admin 角色 id + 新端点 /roles/options
roles = call("GET", "/roles")
check("GET /roles", roles["code"] == 0, roles)
admin_role_id = next(r["id"] for r in roles["data"]["items"] if r["code"] == "admin")
opts = call("GET", "/roles/options")
check("GET /roles/options（新端点）", opts["code"] == 0 and any(o["id"] == admin_role_id for o in opts["data"]["items"]), opts)

# 2. 凭据：复用或创建
creds = call("GET", "/credentials?page=1&page_size=100")
cred = next((c for c in creds["data"]["items"] if c["name"] == "smoke-v2-cred"), None)
if not cred:
    r = call("POST", "/credentials", {"name": "smoke-v2-cred", "login_user": "root", "auth_type": "password", "secret": "smoke-only-not-real"})
    check("POST /credentials", r["code"] == 0, r)
    cred = r["data"]
cred_id = cred["id"]

# 3. 主机 + 应用（应用仅可关联 prod 主机；无主机的应用提交工单会被拒）
hosts = call("GET", "/cmdb/hosts?page=1&page_size=100&keyword=smoke-v2-host")
host = next((h for h in hosts["data"]["items"] if h["hostname"] == "smoke-v2-host"), None)
if not host:
    r = call("POST", "/cmdb/hosts", {"hostname": "smoke-v2-host", "ip": "10.255.255.1", "environment": "prod"})
    check("POST /cmdb/hosts", r["code"] == 0, r)
    host = r["data"]
elif host["environment"] != "prod":
    r = call("PUT", f"/cmdb/hosts/{host['id']}", {"hostname": "smoke-v2-host", "ip": "10.255.255.1", "environment": "prod"})
    check("PUT /cmdb/hosts（改 prod）", r["code"] == 0, r)
apps = call("GET", "/cmdb/apps?page=1&page_size=100&keyword=smoke-v2-app")
app = next((a for a in apps["data"]["items"] if a["name"] == "smoke-v2-app"), None)
if not app:
    r = call("POST", "/cmdb/apps", {"name": "smoke-v2-app", "deploy_type": "shell", "host_ids": [host["id"]]})
    check("POST /cmdb/apps", r["code"] == 0, r)
    app = r["data"]

# 4. 模板：创建（需审批 1 节点 + 必填参数 + fixed 参数 + 通知规则）；名称带时间戳保证可重跑
tpl_name = f"smoke-v2-模板冒烟-{int(time.time())}"
tpl_body = {
    "name": tpl_name, "type": "ops", "description": "冒烟专用", "app_id": app["id"],
    "steps": [{
        "name": "部署", "script_type": "shell", "content": "echo {{ pkg }} to {{ env }}",
        "params_schema": [
            {"name": "pkg", "label": "包版本", "required": True},
            {"name": "env", "default": "prod", "fixed": True},
        ],
        "credential_id": cred_id, "timeout": 300,
    }],
    "approval_enabled": True,
    "approval_nodes": [{"node_order": 1, "role_id": admin_role_id, "approve_mode": "any"}],
    "notify_rules": [{"event": "execution.success", "receivers": ["creator"], "channels": ["email"]}],
}
r = call("POST", "/templates", tpl_body)
check("POST /templates", r["code"] == 0 and r["data"]["id"], r)
tpl_id = r["data"]["id"]

detail = call("GET", f"/templates/{tpl_id}")
check("GET /templates/{id}", detail["code"] == 0 and detail["data"]["current_version"] == 1
      and len(detail["data"]["steps"]) == 1
      and detail["data"]["approval_nodes"][0]["role_id"] == admin_role_id, detail)

# 5. 编辑规则（timeout 变更）→ 自动升版
tpl_body["steps"][0]["timeout"] = 600
tpl_body["changelog"] = "冒烟：超时 300→600"
r = call("PUT", f"/templates/{tpl_id}", tpl_body)
check("PUT /templates 升版", r["code"] == 0 and r["data"]["version_bumped"] and r["data"]["current_version"] == 2, r)
vers = call("GET", f"/templates/{tpl_id}/versions")
check("GET versions", vers["code"] == 0 and len(vers["data"]["items"]) == 2, vers)
snap = call("GET", f"/templates/{tpl_id}/versions/1")
check("GET version snapshot", snap["code"] == 0 and snap["data"]["snapshot"]["steps"][0]["timeout"] == 300, snap)

# 6. 工单中心：可用模板 + 表单描述（fixed 参数不外发）
usable = call("GET", "/tickets/templates")
check("GET /tickets/templates", usable["code"] == 0 and any(t["id"] == tpl_id for t in usable["data"]["items"]), usable)
form = call("GET", f"/tickets/templates/{tpl_id}/form")
pnames = [p["name"] for p in form["data"]["params"]]
check("GET form（fixed 不外发）", form["code"] == 0 and pnames == ["pkg"] and form["data"]["flow"], form)

# 7. 提交工单 → approving，节点 1
r = call("POST", "/tickets", {"template_id": tpl_id, "params": {"pkg": "1.0.0"}})
check("POST /tickets", r["code"] == 0 and r["data"]["status"] == "approving" and r["data"]["current_node"] == 1, r)
tid = r["data"]["id"]
td = call("GET", f"/tickets/{tid}")
check("GET /tickets/{id}（fixed 参数生效）", td["code"] == 0 and td["data"]["params"] == {"pkg": "1.0.0"}
      and td["data"]["steps"][0]["params"].get("env") == "prod"
      and td["data"]["template_version"] == 2, td)

# 8. 待办 + 审批通过 → running
todo = call("GET", "/tickets/todo?page=1&page_size=10")
check("GET /tickets/todo", todo["code"] == 0 and any(t["id"] == tid for t in todo["data"]["items"]), todo)
r = call("POST", f"/tickets/{tid}/approve", {"action": "approve", "comment": "冒烟通过"})
check("POST approve → running", r["code"] == 0 and r["data"]["status"] == "running", r)

# 9. 再提交一单 → 撤回 → cancelled
r = call("POST", "/tickets", {"template_id": tpl_id, "params": {"pkg": "1.0.1"}})
tid2 = r["data"]["id"]
r = call("POST", f"/tickets/{tid2}/cancel")
check("POST cancel → cancelled", r["code"] == 0 and r["data"]["status"] == "cancelled", r)

# 10. 收尾：禁用模板（不删除，保留工单关联），禁用后不可再提交
r = call("PUT", f"/templates/{tpl_id}/status", {"status": "disabled"})
check("PUT status disabled", r["code"] == 0, r)
r = call("POST", "/tickets", {"template_id": tpl_id, "params": {"pkg": "1.0.2"}})
check("禁用后提交被拒", r["code"] != 0, r)

print(f"\nSMOKE PASSED: {len(PASS)} checks")
