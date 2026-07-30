# -*- coding: utf-8 -*-
"""M8-2 压测执行：三渠道通知指向模拟端点 + 500 台单工单提交/审批/执行/验证（api 容器内执行）。

用法：docker cp deploy/loadtest/run_loadtest.py opspilot-api:/tmp/ \
      && docker exec -e PYTHONPATH=/app -w /app opspilot-api python /tmp/run_loadtest.py

前置：seed_loadtest.py 已成功（模板 LT-压测-Shell+Ansible / 应用 lt-app-500 / 作业主机就绪）。
通知模拟端点（冲突声明 C1）：
- email   → lt-mailpit:1025（明文 SMTP，必须 use_tls=false）
- webhook → http://lt-webhook:9000/hook
- teams   → http://lt-webhook:9000/teams
验证以端点实际收到为准（mailpit API / webhook-echo /stats）。
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from app.core.security import create_token

BASE = "http://localhost:8000/api/v1"
TOKEN = create_token(1, "admin", "access")[0]


def call(method: str, path: str, body: dict | None = None, base: str = BASE) -> dict:
    """带 token 调 API（base 可换为模拟端点做直连校验）。"""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def check(name: str, cond: bool, ctx: object = "") -> None:
    """断言小助手：失败即退出。"""
    if not cond:
        print(f"[FAIL] {name} :: {ctx}")
        sys.exit(1)
    print(f"[ok] {name}")


# 1. admin 必须有邮箱（email 渠道 receivers=creator 解析需要）
users = call("GET", "/users?page=1&page_size=50")
admin = next(u for u in users["data"]["items"] if u["username"] == "admin")
if not admin.get("email"):
    r = call("PUT", f"/users/{admin['id']}", {"email": "admin@opspilot.local"})
    check("PUT admin email", r["code"] == 0, r)
else:
    print(f"[skip] admin 邮箱已设置：{admin['email']}")

# 2. 三渠道指向模拟端点并启用
channels = {
    "email": {"enabled": True, "config": {"host": "lt-mailpit", "port": 1025,
                                          "from_addr": "ops@opspilot.local", "use_tls": False}},
    "webhook": {"enabled": True, "config": {"url": "http://lt-webhook:9000/hook"}},
    "teams": {"enabled": True, "config": {"url": "http://lt-webhook:9000/teams"}},
}
for ctype, body in channels.items():
    r = call("PUT", f"/notify/channels/{ctype}", body)
    check(f"PUT /notify/channels/{ctype}", r["code"] == 0, r)
    # 测试端点按请求体 config 发送（保存前预测形态），email 需显式 receiver
    test_body: dict = {"config": body["config"]}
    if ctype == "email":
        test_body["receiver"] = "admin@opspilot.local"
    t = call("POST", f"/notify/channels/{ctype}/test", test_body)
    check(f"渠道测试 {ctype} 发送成功", t["code"] == 0 and t["data"]["success"], t)

# 3. 记录端点基线计数（压测后按增量判断通知落地）
wh0 = call("GET", "/stats", base="http://lt-webhook:9000")
mp0 = call("GET", "/api/v1/messages?limit=1", base="http://lt-mailpit:8025")
base_wh, base_mail = dict(wh0.get("counts", {})), mp0.get("total", 0)
print(f"基线：webhook counts={base_wh} mailpit total={base_mail}")

# 4. 提交工单（模板按名称查，避免硬编码 id）
tpls = call("GET", "/templates?page=1&page_size=100&keyword=" + urllib.parse.quote("LT-压测"))
tpl = next(x for x in tpls["data"]["items"] if x["name"] == "LT-压测-Shell+Ansible")
r = call("POST", "/tickets", {"template_id": tpl["id"], "params": {"tag": "m8"}})
check("POST /tickets", r["code"] == 0, r)
ticket = r["data"]
ticket_id, ticket_no = ticket["id"], ticket["ticket_no"]
print(f"工单 {ticket_no} id={ticket_id}")

# 5. 审批通过（模板审批节点 admin any，审批人即 admin 本人）
r = call("POST", f"/tickets/{ticket_id}/approve", {"action": "approve", "comment": "M8 压测放行"})
check("审批通过", r["code"] == 0, r)

# 6. 轮询执行至终态（500 台 × 2 步，上限 30 分钟）
t_start = time.time()
exe = None
deadline = t_start + 1800
last_status = ""
while time.time() < deadline:
    r = call("GET", f"/executions?page=1&page_size=5&ticket_no={ticket_no}")
    items = r["data"]["items"]
    if items:
        exe = items[0]
        if exe["status"] != last_status:
            last_status = exe["status"]
            print(f"[{time.time() - t_start:6.1f}s] 执行状态：{last_status}")
        if exe["status"] in ("success", "failed", "aborted"):
            break
    time.sleep(5)
check("执行进入终态", exe is not None and exe["status"] in ("success", "failed", "aborted"),
      exe and exe.get("status"))
elapsed = time.time() - t_start

# 7. 执行结果统计（步骤/主机成败）
detail = call("GET", f"/executions/{exe['id']}")["data"]
print(f"\n===== 压测执行报告（工单 {ticket_no}）=====")
print(f"最终状态：{exe['status']}  总耗时：{elapsed:.1f}s（审批→终态）")
for step in detail.get("steps", []):
    print(f"  步骤[{step.get('step_order')}] {step.get('name')} 状态={step.get('status')} "
          f"成功={step.get('success_count')} 失败={step.get('failed_count')}")

# 8. 通知落端点验证（增量 > 0 即达标：email/webhook/teams 各至少一次，DoD-3）
time.sleep(5)  # 通知异步发送，留缓冲
wh1 = call("GET", "/stats", base="http://lt-webhook:9000")
mp1 = call("GET", "/api/v1/messages?limit=1", base="http://lt-mailpit:8025")
hook_inc = wh1["counts"].get("/hook", 0) - base_wh.get("/hook", 0)
teams_inc = wh1["counts"].get("/teams", 0) - base_wh.get("/teams", 0)
mail_inc = mp1.get("total", 0) - base_mail
print(f"\n通知增量：email={mail_inc} webhook={hook_inc} teams={teams_inc}")
check("email 通知落 mailpit", mail_inc >= 1, mp1)
check("webhook 通知落 echo", hook_inc >= 1, wh1)
check("teams 通知落 echo", teams_inc >= 1, wh1)
print("\nRUN DONE：500 台 Shell+Ansible 压测执行 + 三渠道通知全部落端点")
