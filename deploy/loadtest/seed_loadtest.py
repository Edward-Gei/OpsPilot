# -*- coding: utf-8 -*-
"""M8-2 压测种子：1000 台主机 Excel 导入 + 应用关联 500 台 + 压测模板（在 api 容器内执行）。

用法：docker cp deploy/loadtest/seed_loadtest.py opspilot-api:/tmp/ \
      && docker exec opspilot-api python /tmp/seed_loadtest.py

数据规划（冲突声明 C2/C3）：
- lt-real-001..500：IP 172.28.4.1..172.28.5.250（sshd 矩阵 secondary IP，可真实建连）
- lt-fake-501..1000：IP 10.254.x.y（不可达，仅撑数据规模验证导入与列表 P95）
- 应用 lt-app-500 关联全部 500 台真实主机；
- 模板「LT-压测-Shell+Ansible」：步骤1 Shell（echo+sleep 1）+ 步骤2 Ansible ping，
  审批开启（admin any）、并发 50、通知 execution.success/failed → email+webhook+teams。
幂等：lt- 前缀资源存在即复用，可重复执行。
"""
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

from openpyxl import Workbook

from app.core.security import create_token

BASE = "http://localhost:8000/api/v1"
TOKEN = create_token(1, "admin", "access")[0]

REAL_COUNT = 500          # 可真实建连主机数（对应 sshd 矩阵 IP_COUNT）
TOTAL_COUNT = 1000        # CMDB 总规模（DoD-2）
SSH_PASSWORD = "Loadtest123"  # 与 sshd 矩阵容器 ROOT_PASSWORD 一致


def call(method: str, path: str, body: dict | None = None,
         raw: bytes | None = None, content_type: str | None = None) -> dict:
    """带 token 调 API；raw 用于 multipart 上传。"""
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if content_type:
        headers["Content-Type"] = content_type
    elif body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def check(name: str, cond: bool, ctx: object = "") -> None:
    """断言小助手：失败即退出（种子必须完整成功）。"""
    if not cond:
        print(f"[FAIL] {name} :: {ctx}")
        sys.exit(1)
    print(f"[ok] {name}")


def real_ip(seq: int) -> str:
    """第 seq 台（1 起）真实主机 IP：172.28.4.1..172.28.4.250 → 172.28.5.x（与 entrypoint 一致）。"""
    block, off = divmod(seq - 1, 250)
    return f"172.28.{4 + block}.{off + 1}"


def build_hosts_xlsx() -> bytes:
    """按导入模板列序生成 1000 行主机 Excel（真实走 /cmdb/hosts/import 通道）。"""
    wb = Workbook()
    ws = wb.active
    # 列序与 host_excel.COLUMNS 对齐（主机名/IP/平台/区域/OS/CPU/内存/磁盘/环境/状态/端口/说明）
    ws.append(["主机名*", "IP地址*", "所属平台", "所属区域", "操作系统",
               "CPU核数", "内存GB", "磁盘GB", "环境*", "状态", "SSH端口", "说明"])
    for i in range(1, TOTAL_COUNT + 1):
        if i <= REAL_COUNT:
            hostname, ip, desc = f"lt-real-{i:03d}", real_ip(i), "压测真实可连"
        else:
            hostname = f"lt-fake-{i:03d}"
            ip = f"10.254.{(i - REAL_COUNT - 1) // 250}.{(i - REAL_COUNT - 1) % 250 + 1}"
            desc = "压测数据规模占位"
        ws.append([hostname, ip, "loadtest", "lab", "debian12",
                   2, 4, 40, "prod", "online", 22, desc])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def multipart(field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    """手工构造单文件 multipart/form-data（容器内无 requests）。"""
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
        f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


# 1. 凭据（root 密码与 sshd 矩阵一致）
creds = call("GET", "/credentials?page=1&page_size=100")
cred = next((c for c in creds["data"]["items"] if c["name"] == "lt-cred"), None)
if not cred:
    r = call("POST", "/credentials", {"name": "lt-cred", "login_user": "root",
                                      "auth_type": "password", "secret": SSH_PASSWORD})
    check("POST /credentials lt-cred", r["code"] == 0, r)
    cred = r["data"]
cred_id = cred["id"]
print(f"凭据 lt-cred id={cred_id}")

# 2. 1000 台主机：已导入则跳过；否则 Excel 一次性导入（DoD-2 验证通道）
exist = call("GET", "/cmdb/hosts?page=1&page_size=1&keyword=lt-real-")
if exist["data"]["total"] >= REAL_COUNT:
    print(f"[skip] 已存在 lt-real-* 主机 {exist['data']['total']} 台，跳过导入")
else:
    raw, ctype = multipart("file", "loadtest_hosts.xlsx", build_hosts_xlsx())
    r = call("POST", "/cmdb/hosts/import", raw=raw, content_type=ctype)
    check("POST /cmdb/hosts/import 1000 台", r["code"] == 0, r)
    print(f"导入结果：{r['data']}")

# 3. 应用关联 500 台真实主机
host_ids: list[int] = []
page = 1
while len(host_ids) < REAL_COUNT:
    r = call("GET", f"/cmdb/hosts?page={page}&page_size=100&keyword=lt-real-")
    items = r["data"]["items"]
    if not items:
        break
    host_ids.extend(h["id"] for h in items)
    page += 1
check(f"lt-real 主机数=={REAL_COUNT}", len(host_ids) == REAL_COUNT, len(host_ids))

apps = call("GET", "/cmdb/apps?page=1&page_size=100&keyword=lt-app-500")
app = next((a for a in apps["data"]["items"] if a["name"] == "lt-app-500"), None)
if not app:
    r = call("POST", "/cmdb/apps", {"name": "lt-app-500", "deploy_type": "shell",
                                    "description": "M8 压测：500 台真实可连", "host_ids": host_ids})
    check("POST /cmdb/apps lt-app-500", r["code"] == 0, r)
    app = r["data"]
print(f"应用 lt-app-500 id={app['id']}")

# 4. Ansible 作业主机配置（sshd 矩阵容器固定 IP，DoD-3 前置）
r = call("PUT", "/system/configs", {"configs": {"ansible.job_host": {
    "ip": "172.28.0.10", "port": 22, "credential_id": cred_id, "workdir": "/tmp"}}})
check("PUT ansible.job_host", r["code"] == 0, r)
t = call("POST", "/system/ansible-job-host/test",
         {"ip": "172.28.0.10", "port": 22, "credential_id": cred_id})
check("作业主机连通性测试", t["code"] == 0, t)
print(f"ansible: {t['data']}")

# 5. 压测模板：Shell + Ansible 两步；并发 50；审批 admin any；三渠道通知
roles = call("GET", "/roles")
admin_role_id = next(x["id"] for x in roles["data"]["items"] if x["code"] == "admin")
tpls = call("GET", "/templates?page=1&page_size=100&keyword=" + urllib.parse.quote("LT-压测"))
tpl = next((x for x in tpls["data"]["items"] if x["name"] == "LT-压测-Shell+Ansible"), None)
if not tpl:
    playbook = (
        "---\n- hosts: targets\n  gather_facts: no\n  tasks:\n"
        "    - name: ping\n      ansible.builtin.ping:\n"
    )
    body = {
        "name": "LT-压测-Shell+Ansible", "type": "ops",
        "description": "M8-2：500 台单工单 Shell+Ansible 压测", "app_id": app["id"],
        "steps": [
            {"name": "Shell 回显", "script_type": "shell",
             "content": "echo lt-{{ tag }} on $(hostname); sleep 1",
             "params_schema": [{"name": "tag", "label": "批次标记", "required": True}],
             "credential_id": cred_id, "timeout": 600},
            {"name": "Ansible ping", "script_type": "playbook", "content": playbook,
             "params_schema": [], "credential_id": cred_id, "timeout": 900},
        ],
        # 执行策略在模板级：并发 50（顶到全局信号量上限）、不分批、失败不中断
        "exec_strategy": {"concurrency": 50, "batch_size": 0, "fail_fast": False,
                          "timeout": 900},
        "approval_enabled": True,
        "approval_nodes": [{"node_order": 1, "role_id": admin_role_id, "approve_mode": "any"}],
        "notify_rules": [
            {"event": "execution.success", "receivers": ["creator"],
             "channels": ["email", "webhook", "teams"]},
            {"event": "execution.failed", "receivers": ["creator"],
             "channels": ["email", "webhook", "teams"]},
        ],
    }
    r = call("POST", "/templates", body)
    check("POST /templates LT-压测", r["code"] == 0, r)
    tpl = r["data"]
print(f"模板 id={tpl['id']}")
print("\nSEED DONE：主机 1000（真实 500）/ 应用 lt-app-500 / 模板就绪")
