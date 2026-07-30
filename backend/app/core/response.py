"""统一响应包裹与业务异常。

响应格式（见 04-API设计 §1）：
    成功: {"code": 0, "message": "ok", "data": ...}
    失败: {"code": 4xxxx, "message": "...", "data": null}
"""
from typing import Any

from fastapi.responses import JSONResponse


class BizError(Exception):
    """业务异常：携带业务 code 与 HTTP 状态码，由全局异常处理器统一转响应。

    data 用于三态登录等需要随错误码回传载荷的场景（04-API设计 §2：
    40103/40104 携带 mfa_token，40105 携带 change_token）。
    """

    def __init__(self, code: int, message: str, http_status: int = 400, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.data = data


# ---- 常用业务异常工厂（错误码对齐 04-API设计 §1）----

class Errors:
    """错误码常量与快捷构造器。"""

    PARAM = 40001            # 参数校验失败
    UNAUTHORIZED = 40101     # 未认证/Token 无效
    TOKEN_EXPIRED = 40102    # Token 过期
    MFA_REQUIRED = 40103     # 需要 MFA 验证
    MFA_SETUP = 40104        # 需要绑定 MFA
    PASSWORD_CHANGE = 40105  # 需要修改初始密码
    NO_PERM = 40301          # 无权限点
    OBJECT_DENIED = 40302    # 对象级越权
    NOT_FOUND = 40401        # 资源不存在
    CONFLICT = 40901         # 状态冲突
    BIZ_REJECTED = 42201     # 业务规则拒绝（如删除保护）
    LOCKED = 42901           # 账号锁定/频率限制
    INTERNAL = 50001         # 服务内部错误

    @staticmethod
    def param(msg: str) -> BizError:
        return BizError(Errors.PARAM, msg, 400)

    @staticmethod
    def not_found(msg: str = "资源不存在") -> BizError:
        return BizError(Errors.NOT_FOUND, msg, 404)

    @staticmethod
    def conflict(msg: str) -> BizError:
        return BizError(Errors.CONFLICT, msg, 409)

    @staticmethod
    def rejected(msg: str) -> BizError:
        return BizError(Errors.BIZ_REJECTED, msg, 422)


def ok(data: Any = None, message: str = "ok") -> dict:
    """成功响应体。"""
    return {"code": 0, "message": message, "data": data}


def fail_response(code: int, message: str, http_status: int, data: Any = None) -> JSONResponse:
    """失败响应（供异常处理器使用）；data 用于三态登录等需要回传载荷的错误。"""
    return JSONResponse(
        status_code=http_status,
        content={"code": code, "message": message, "data": data},
    )
