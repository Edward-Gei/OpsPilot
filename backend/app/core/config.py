"""全局配置：统一从环境变量 / .env 加载，.env.example 为配置清单唯一来源。"""
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """平台配置项。字段名与环境变量同名（大小写不敏感）。"""

    # 基础
    app_name: str = "OpsPilot"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # MySQL
    mysql_host: str = "mysql"
    mysql_port: int = 3306
    mysql_user: str = "opspilot"
    mysql_password: str = "opspilot"
    mysql_db: str = "opspilot"

    # Redis（内网部署，默认无密码）
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0

    # 安全密钥
    jwt_secret_key: str = "change-me-in-env"          # JWT 签名密钥
    secret_encrypt_key: str = ""                       # AES-256-GCM 主密钥（32字节 base64，M1 起用于 MFA secret 加密；留空则从 JWT_SECRET_KEY 派生并告警）

    # 令牌时效（02-技术架构 §3.1：Access 15min / Refresh 7d / mfa_token 短时）
    access_token_minutes: int = 15                     # Access Token 有效期（分钟）
    refresh_token_days: int = 7                        # Refresh Token 有效期（天）
    mfa_token_minutes: int = 5                         # MFA 挑战令牌有效期（分钟，仅限 /auth/mfa/*）
    pwd_change_token_minutes: int = 10                 # 强制改密令牌有效期（分钟，仅限 PUT /auth/password）

    # 审计与执行
    audit_retention_days: int = 365                    # 审计日志保留天数（按 PRD 放配置文件）
    exec_log_dir: str = "/data/logs"                   # 执行日志落盘目录（共享卷）
    exec_global_concurrency_default: int = 50          # 全局 SSH 并发默认值

    # 初始 admin 密码（留空则随机生成并打印到容器日志）
    admin_initial_password: str = ""

    # 开发跨域（生产走 nginx 同源，无需配置）
    cors_origins: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def async_database_url(self) -> str:
        """运行时异步连接串（asyncmy）。"""
        return (
            f"mysql+asyncmy://{self.mysql_user}:{quote_plus(self.mysql_password)}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}?charset=utf8mb4"
        )

    @property
    def sync_database_url(self) -> str:
        """Alembic 迁移用同步连接串（pymysql，见冲突声明 C2）。"""
        return (
            f"mysql+pymysql://{self.mysql_user}:{quote_plus(self.mysql_password)}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    """配置单例：进程内只解析一次环境变量。"""
    return Settings()


settings = get_settings()
