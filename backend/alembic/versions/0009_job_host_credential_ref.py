"""凭据打通：作业主机改为引用凭据；模板/工单新增引用凭据列。

变更内容（对齐设计文档 2026-07-31-credential-integration）：
    - job_host：+credential_id / -login_user -auth_type -secret_enc -passphrase_enc
      （存量为测试数据，直接删列不迁移；存量主机需重新关联凭据）
    - ticket_template：+credential_refs JSON（[{alias, credential_id}]）
    - ticket：+credential_refs JSON（提单快照 [{alias, credential_id, credential_name}]）

幂等策略：与 0007/0008 一致，先查 information_schema 判断列是否存在再执行，
全新库（0001 create_all 即新结构）与已升级库重复执行结果一致。
"""
from alembic import op
from sqlalchemy import text

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _column_exists(bind, table: str, column: str) -> bool:
    """判断列是否存在（当前库）。"""
    return bool(
        bind.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    # 1. job_host：+credential_id，删认证四列
    if not _column_exists(bind, "job_host", "credential_id"):
        op.execute(
            "ALTER TABLE job_host ADD COLUMN credential_id BIGINT UNSIGNED NULL "
            "COMMENT '关联凭据 credential.id（登录认证随凭据管理；API 层必填）'"
        )
    for col in ("login_user", "auth_type", "secret_enc", "passphrase_enc"):
        if _column_exists(bind, "job_host", col):
            op.execute(f"ALTER TABLE job_host DROP COLUMN {col}")
    # 2. ticket_template：+credential_refs
    if not _column_exists(bind, "ticket_template", "credential_refs"):
        op.execute(
            "ALTER TABLE ticket_template ADD COLUMN credential_refs JSON NULL "
            "COMMENT '引用凭据 [{alias, credential_id}]'"
        )
    # 3. ticket：+credential_refs
    if not _column_exists(bind, "ticket", "credential_refs"):
        op.execute(
            "ALTER TABLE ticket ADD COLUMN credential_refs JSON NULL "
            "COMMENT '引用凭据快照 [{alias, credential_id, credential_name}]'"
        )


def downgrade() -> None:
    # 认证列数据已删除无法恢复，不提供回滚
    pass
