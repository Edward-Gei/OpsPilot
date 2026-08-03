"""Redis 接入：连接池 + Stream / PubSub 轻封装。

键名约定（见 02-技术架构设计 §3/§4）：
    ops:exec:queue          执行任务队列（Stream）
    ops:notify:queue        通知任务队列（Stream）
    ops:log:{eid}           执行日志通道（PubSub）
    ops:event:{eid}         执行事件通道（PubSub）
    ops:ctrl:{eid}          运行时控制信号（String + PubSub）
    login:fail:{username}   登录失败计数（架构文档指定键名）
    auth:revoked:{jti}      已吊销 Refresh Token（TTL=剩余有效期）
    auth:perms:{user_id}    用户权限集合缓存（角色变更时失效）
    auth:mfa:pending:{uid}  MFA 绑定中的待确认 secret（TTL 600s）
    auth:oidc:state:{state} OIDC 授权码流程 state 防伪造（TTL 600s）
    auth:sso:ticket:{tk}    SSO 回调一次性换票（TTL 60s）
    pwd_reset:{user_id}    密码重置 token 哈希（TTL 配置）
    pwd_reset:lookup:{hash} token 哈希到用户 ID 索引（TTL 配置）
    auth:refresh:{user_id} 用户 Refresh jti 集合
"""
import redis.asyncio as aioredis

from app.core.config import settings

# 全局共享异步客户端（redis-py 自带连接池）
redis_client: aioredis.Redis = aioredis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
    max_connections=50,
)

# Stream 键名常量
EXEC_QUEUE = "ops:exec:queue"
NOTIFY_QUEUE = "ops:notify:queue"
EXEC_CONSUMER_GROUP = "exec-workers"

# 执行引擎键名模板（M5）
KEY_EXEC_CTRL = "ops:ctrl:{eid}"          # 控制信号（String + PubSub）
KEY_EXEC_LOG = "ops:log:{eid}"            # 实时日志通道（PubSub）
KEY_EXEC_EVENT = "ops:event:{eid}"        # 状态事件通道（PubSub）
KEY_EXEC_EVENT_LIST = "ops:events:{eid}"  # 事件回放列表（List，长轮询/WS 断线补发）
KEY_EXEC_EVENT_SEQ = "ops:event:seq:{eid}"  # 事件序号发号器（INCR，单调递增）

# 认证相关键名模板（M1）
KEY_LOGIN_FAIL = "login:fail:{username}"
KEY_TOKEN_REVOKED = "auth:revoked:{jti}"
KEY_USER_PERMS = "auth:perms:{user_id}"
KEY_MFA_PENDING = "auth:mfa:pending:{user_id}"
KEY_OIDC_STATE = "auth:oidc:state:{state}"
KEY_SSO_TICKET = "auth:sso:ticket:{ticket}"
KEY_PWD_RESET = "pwd_reset:{user_id}"
KEY_PWD_RESET_LOOKUP = "pwd_reset:lookup:{token_hash}"
KEY_PWD_RESET_USED = "pwd_reset:used:{token_hash}"
KEY_PWD_RESET_EMAIL_COOLDOWN = "pwd_reset:email:cooldown:{email}"
KEY_PWD_RESET_EMAIL_DAILY = "pwd_reset:email:daily:{date}:{email}"
KEY_PWD_RESET_IP_HOURLY = "pwd_reset:ip:hourly:{hour}:{ip}"
KEY_PWD_RESET_NOTIFICATION_IP = "pwd_reset:notification_ip:{record_id}"
KEY_USER_REFRESH = "auth:refresh:{user_id}"



async def ensure_stream_group(stream: str, group: str) -> None:
    """幂等创建 Stream 消费组（组已存在时忽略 BUSYGROUP 错误）。"""
    try:
        await redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    except aioredis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def ping() -> bool:
    """健康检查探活。"""
    return await redis_client.ping()
