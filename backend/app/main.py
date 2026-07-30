"""API 服务入口：FastAPI 应用工厂 + 全局异常处理 + 健康检查。

启动流程：docker-entrypoint.sh 先执行 Alembic 迁移（仅 api 容器），
再启动 uvicorn；lifespan 内执行幂等种子数据。
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1 import api_router
from app.api.ws import router as ws_router
from app.audit import audit_writer
from app.core import redis as redis_mod
from app.core.config import settings
from app.core.database import async_session_factory, engine
from app.core.response import BizError, Errors, fail_response, ok
from app.services.seed import run_seed

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("opspilot.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时执行种子数据与审计写入器，关闭时 flush 并释放连接。"""
    async with async_session_factory() as session:
        await run_seed(session)
    audit_writer.start()
    logger.info("%s API 启动完成", settings.app_name)
    yield
    await audit_writer.stop()
    await engine.dispose()
    await redis_mod.redis_client.aclose()


def create_app() -> FastAPI:
    """应用工厂：集中注册中间件 / 异常处理 / 路由。"""
    app = FastAPI(
        title=f"{settings.app_name} API",
        lifespan=lifespan,
        docs_url=f"{settings.api_prefix}/docs",
        openapi_url=f"{settings.api_prefix}/openapi.json",
    )

    # 开发跨域（生产 nginx 同源代理，不命中）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- 全局异常处理：任何异常都转统一响应包裹 ----

    @app.exception_handler(BizError)
    async def biz_error_handler(_: Request, exc: BizError):
        """业务异常 -> 对应 HTTP 状态码与业务 code；data 透传三态登录载荷。"""
        return fail_response(exc.code, exc.message, exc.http_status, exc.data)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError):
        """参数校验失败 -> 40001，取第一条错误便于前端直接展示。"""
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []))
        return fail_response(Errors.PARAM, f"参数校验失败: {loc} {first.get('msg', '')}", 422)

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception):
        """兜底异常 -> 50001，详情只进日志不外露。"""
        logger.exception("未处理异常: %s", exc)
        return fail_response(Errors.INTERNAL, "服务内部错误", 500)

    # ---- 健康检查（compose healthcheck / nginx 探活使用） ----

    @app.get("/healthz", summary="健康检查")
    async def healthz():
        """探活 MySQL 与 Redis，任一不可用返回 503。"""
        checks: dict[str, str] = {}
        healthy = True
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["mysql"] = "up"
        except Exception as exc:  # noqa: BLE001 探活失败原因需要透出
            checks["mysql"] = f"down: {exc.__class__.__name__}"
            healthy = False
        try:
            await redis_mod.ping()
            checks["redis"] = "up"
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = f"down: {exc.__class__.__name__}"
            healthy = False
        if not healthy:
            return fail_response(Errors.INTERNAL, str(checks), 503)
        return ok(checks)

    app.include_router(api_router, prefix=settings.api_prefix)
    # WS 网关挂应用根路径（nginx /ws/ 代理直达，不过 api_prefix）
    app.include_router(ws_router)
    return app


app = create_app()
