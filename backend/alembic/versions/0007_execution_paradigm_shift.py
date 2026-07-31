"""执行范式改造：平台只调度作业主机（Jenkins agent），不再批量对目标主机执行。

变更内容（对齐 Task #1 模型变更）：
    - 新建 job_host 表（作业主机，独立管理 SSH 连接信息）
    - ticket_template：+job_host_id / -app_id / type 归并 daily_ops / exec_strategy 只留 timeout+fail_fast
    - template_step：-credential_id（凭据随作业主机走，步骤不再单独配置）
    - ticket：+job_host_id +job_host_snap / -app_id -app_name_snap / type 归并 daily_ops
    - execution：-total_hosts；execution_step：删批次/主机计数列，+exit_code +error_summary
    - 删除 ticket_host / execution_step_host 表；清理 system_config 的 ansible.job_host 键
    - template_version.snapshot 中的 type 同步归并为 daily_ops

幂等策略：全新库执行 0001 create_all 时已是新结构，本迁移所有 DDL 均先查
information_schema 判断表/列/索引是否存在再执行，重复执行结果一致。
数据迁移：若存量库配置过 ansible.job_host，则据此生成一条"默认作业主机"记录
并回填 ticket_template.job_host_id；否则保持 0，由管理员后续手动配置。
"""
import json

from alembic import op
from sqlalchemy import text

from app.models import Base

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _table_exists(bind, table: str) -> bool:
    """判断表是否存在（当前库）。"""
    return bool(
        bind.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :t"
            ),
            {"t": table},
        ).scalar()
    )


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


def _index_exists(bind, table: str, index: str) -> bool:
    """判断索引是否存在（当前库）。"""
    return bool(
        bind.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"
            ),
            {"t": table, "i": index},
        ).scalar()
    )


def _as_dict(raw) -> dict | None:
    """JSON 列取值兼容：驱动可能返回 str（pymysql）或已解析的 dict。"""
    if raw is None:
        return None
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    return raw if isinstance(raw, dict) else None


def _migrate_default_job_host(bind) -> None:
    """数据迁移：从 system_config 的 ansible.job_host 生成默认作业主机并回填模板。

    幂等：以 name='默认作业主机' 是否已存在为准，重复执行不会重复插入。
    """
    raw = bind.execute(
        text("SELECT cfg_value FROM system_config WHERE cfg_key = 'ansible.job_host'")
    ).scalar()
    cfg = _as_dict(raw)
    # cfg_value 统一包 {"value": ...}，未配置或值为 null 则跳过
    jh_cfg = _as_dict(cfg.get("value")) if cfg else None
    if not jh_cfg or not jh_cfg.get("ip"):
        return
    # 查关联凭据，取 SSH 登录信息；凭据缺失则无法组装完整记录，跳过
    cred = bind.execute(
        text(
            "SELECT login_user, auth_type, secret_enc, passphrase_enc "
            "FROM credential WHERE id = :cid"
        ),
        {"cid": jh_cfg.get("credential_id") or 0},
    ).first()
    if cred is None:
        return
    job_host_id = bind.execute(
        text("SELECT id FROM job_host WHERE name = :n"), {"n": "默认作业主机"}
    ).scalar()
    if job_host_id is None:
        bind.execute(
            text(
                "INSERT INTO job_host "
                "(name, ip, ssh_port, login_user, auth_type, secret_enc, passphrase_enc, workdir, enabled) "
                "VALUES (:name, :ip, :port, :user, :atype, :secret, :passphrase, :workdir, 1)"
            ),
            {
                "name": "默认作业主机",
                "ip": jh_cfg["ip"],
                "port": int(jh_cfg.get("port") or 22),
                "user": cred.login_user,
                "atype": cred.auth_type,
                "secret": cred.secret_enc,
                "passphrase": cred.passphrase_enc,
                "workdir": jh_cfg.get("workdir") or "/opt/opspilot/workspace",
            },
        )
        job_host_id = bind.execute(
            text("SELECT id FROM job_host WHERE name = :n"), {"n": "默认作业主机"}
        ).scalar()
    # 仅回填尚未配置作业主机的模板（job_host_id=0 为临时占位）
    bind.execute(
        text("UPDATE ticket_template SET job_host_id = :jid WHERE job_host_id = 0"),
        {"jid": job_host_id},
    )


def _shrink_exec_strategy(bind) -> None:
    """exec_strategy 瘦身：逐行解析 JSON，只保留 timeout + fail_fast 两键。"""
    rows = bind.execute(
        text("SELECT id, exec_strategy FROM ticket_template WHERE exec_strategy IS NOT NULL")
    ).all()
    for tpl_id, raw in rows:
        data = _as_dict(raw)
        if data is None:
            continue
        kept = {k: v for k, v in data.items() if k in ("timeout", "fail_fast")}
        if kept != data:
            bind.execute(
                text("UPDATE ticket_template SET exec_strategy = :es WHERE id = :id"),
                {"es": json.dumps(kept), "id": tpl_id},
            )


def upgrade() -> None:
    """按依赖顺序执行：建 job_host → 改模板/工单/执行域 → 删旧表 → 清配置。"""
    bind = op.get_bind()

    # 1. 建 job_host 表（全新库 0001 已建则跳过；按 ORM 元数据建表保证结构一致）
    if not _table_exists(bind, "job_host"):
        Base.metadata.create_all(bind=bind, tables=[Base.metadata.tables["job_host"]])

    # 2. ticket_template：加 job_host_id（临时默认 0）→ 数据迁移 → 删 app_id → 归并 type → 瘦身策略 → 换索引
    if not _column_exists(bind, "ticket_template", "job_host_id"):
        op.execute(
            "ALTER TABLE ticket_template ADD COLUMN job_host_id BIGINT UNSIGNED "
            "NOT NULL DEFAULT 0 COMMENT '作业主机'"
        )
    _migrate_default_job_host(bind)
    if _column_exists(bind, "ticket_template", "app_id"):
        op.execute("ALTER TABLE ticket_template DROP COLUMN app_id")
    op.execute("UPDATE ticket_template SET type = 'daily_ops' WHERE type IN ('change', 'ops')")
    _shrink_exec_strategy(bind)
    if _index_exists(bind, "ticket_template", "idx_ticket_template_app"):
        op.execute("ALTER TABLE ticket_template DROP INDEX idx_ticket_template_app")
    if not _index_exists(bind, "ticket_template", "idx_ticket_template_jh"):
        op.execute("ALTER TABLE ticket_template ADD INDEX idx_ticket_template_jh (job_host_id)")

    # 3. template_step：删 credential_id（凭据随作业主机管理）
    if _column_exists(bind, "template_step", "credential_id"):
        op.execute("ALTER TABLE template_step DROP COLUMN credential_id")

    # 4. ticket：加 job_host_id / job_host_snap，删 app_id / app_name_snap，归并 type
    if not _column_exists(bind, "ticket", "job_host_id"):
        op.execute(
            "ALTER TABLE ticket ADD COLUMN job_host_id BIGINT UNSIGNED "
            "NOT NULL DEFAULT 0 COMMENT '作业主机'"
        )
    if not _column_exists(bind, "ticket", "job_host_snap"):
        # MySQL JSON 列不支持字面量 DEFAULT：先加可空列 → 回填历史行 → 再收紧 NOT NULL
        op.execute(
            "ALTER TABLE ticket ADD COLUMN job_host_snap JSON NULL "
            "COMMENT '作业主机快照 {id,name,ip,ssh_port,workdir}'"
        )
        op.execute(
            "UPDATE ticket SET job_host_snap = JSON_OBJECT("
            "'id', 0, 'name', '(已迁移)', 'ip', '', 'ssh_port', 0, 'workdir', '') "
            "WHERE job_host_snap IS NULL"
        )
        op.execute(
            "ALTER TABLE ticket MODIFY COLUMN job_host_snap JSON NOT NULL "
            "COMMENT '作业主机快照 {id,name,ip,ssh_port,workdir}'"
        )
    if _column_exists(bind, "ticket", "app_id"):
        op.execute("ALTER TABLE ticket DROP COLUMN app_id")
    if _column_exists(bind, "ticket", "app_name_snap"):
        op.execute("ALTER TABLE ticket DROP COLUMN app_name_snap")
    op.execute("UPDATE ticket SET type = 'daily_ops' WHERE type IN ('change', 'ops')")

    # 5. 工单目标主机表下线（目标主机概念随范式切换移除）
    op.execute("DROP TABLE IF EXISTS ticket_host")

    # 6. execution：删 total_hosts
    if _column_exists(bind, "execution", "total_hosts"):
        op.execute("ALTER TABLE execution DROP COLUMN total_hosts")

    # 7. execution_step：删批次/主机计数列，加退出码与失败摘要
    for col in ("current_batch", "total_batch", "success_count", "failed_count"):
        if _column_exists(bind, "execution_step", col):
            op.execute(f"ALTER TABLE execution_step DROP COLUMN {col}")
    if not _column_exists(bind, "execution_step", "exit_code"):
        op.execute("ALTER TABLE execution_step ADD COLUMN exit_code INT NULL COMMENT '退出码'")
    if not _column_exists(bind, "execution_step", "error_summary"):
        op.execute(
            "ALTER TABLE execution_step ADD COLUMN error_summary VARCHAR(512) NULL COMMENT '失败摘要'"
        )

    # 8. 逐目标主机执行结果表下线
    op.execute("DROP TABLE IF EXISTS execution_step_host")

    # 9. 清理已废弃的全局作业主机配置键（配置已实体化为 job_host 记录）
    op.execute("DELETE FROM system_config WHERE cfg_key = 'ansible.job_host'")

    # 10. 模板版本快照中的 type 同步归并（快照行不可变，此处为一次性口径修正）
    op.execute(
        "UPDATE template_version SET snapshot = JSON_SET(snapshot, '$.type', 'daily_ops') "
        "WHERE JSON_UNQUOTE(JSON_EXTRACT(snapshot, '$.type')) IN ('change', 'ops')"
    )


def downgrade() -> None:
    """不支持回滚：旧列/旧表数据已删除，无法还原（仅供开发环境重置后重跑 0001）。"""
    raise NotImplementedError("This migration is not reversible")
