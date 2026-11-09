"""数据库管理九表的冻结 DDL；逐表检查避免当前 ORM 初始迁移提前建表。"""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

# 冻结本次模式，不导入可能在后续版本变化的 ORM。
TABLES = [('database_instance',
  'CREATE TABLE database_instance (\n'
  '\tid BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, \n'
  '\tname VARCHAR(128) NOT NULL, \n'
  '\tengine VARCHAR(16) NOT NULL, \n'
  '\tenvironment VARCHAR(16) NOT NULL, \n'
  '\tdescription VARCHAR(255), \n'
  '\tenabled BOOL NOT NULL, \n'
  '\thost VARCHAR(255) NOT NULL, \n'
  '\tport INTEGER NOT NULL, \n'
  '\tcredential_id BIGINT UNSIGNED NOT NULL, \n'
  '\tbusiness_line VARCHAR(16), \n'
  '\tcluster_name VARCHAR(128), \n'
  '\tcluster_role VARCHAR(64), \n'
  '\tversion VARCHAR(128), \n'
  '\tconnection_options JSON NOT NULL, \n'
  '\tconfig_revision INTEGER NOT NULL, \n'
  '\tconcurrency_limit INTEGER, \n'
  '\tlast_probe_at DATETIME(3), \n'
  '\tlast_probe_status VARCHAR(16) NOT NULL, \n'
  '\tlast_probe_message VARCHAR(512), \n'
  '\tcreated_by BIGINT UNSIGNED, \n'
  '\tcreated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), \n'
  '\tupdated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3), \n'
  '\tCONSTRAINT pk_database_instance PRIMARY KEY (id), \n'
  '\tCONSTRAINT uk_database_instance_name UNIQUE (name)\n'
  ')CHARSET=utf8mb4 COLLATE utf8mb4_0900_ai_ci',
  [('id', 'BIGINT UNSIGNED', False),
   ('name', 'VARCHAR(128)', False),
   ('engine', 'VARCHAR(16)', False),
   ('environment', 'VARCHAR(16)', False),
   ('description', 'VARCHAR(255)', True),
   ('enabled', 'BOOL', False),
   ('host', 'VARCHAR(255)', False),
   ('port', 'INTEGER', False),
   ('credential_id', 'BIGINT UNSIGNED', False),
   ('business_line', 'VARCHAR(16)', True),
   ('cluster_name', 'VARCHAR(128)', True),
   ('cluster_role', 'VARCHAR(64)', True),
   ('version', 'VARCHAR(128)', True),
   ('connection_options', 'JSON', False),
   ('config_revision', 'INTEGER', False),
   ('concurrency_limit', 'INTEGER', True),
   ('last_probe_at', 'DATETIME(3)', True),
   ('last_probe_status', 'VARCHAR(16)', False),
   ('last_probe_message', 'VARCHAR(512)', True),
   ('created_by', 'BIGINT UNSIGNED', True),
   ('created_at', 'DATETIME(3)', False),
   ('updated_at', 'DATETIME(3)', False)],
  [('idx_database_instance_credential',
    'CREATE INDEX idx_database_instance_credential ON database_instance (credential_id)'),
   ('idx_database_instance_engine_enabled',
    'CREATE INDEX idx_database_instance_engine_enabled ON database_instance (engine, enabled)'),
   ('idx_database_instance_environment',
    'CREATE INDEX idx_database_instance_environment ON database_instance (environment)')],
  [('name',)]),
 ('database_instance_application',
  'CREATE TABLE database_instance_application (\n'
  '\tid BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, \n'
  '\tinstance_id BIGINT UNSIGNED NOT NULL, \n'
  '\tapp_id BIGINT UNSIGNED NOT NULL, \n'
  '\tcreated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), \n'
  '\tCONSTRAINT pk_database_instance_application PRIMARY KEY (id), \n'
  '\tCONSTRAINT uk_database_instance_application UNIQUE (instance_id, app_id)\n'
  ')CHARSET=utf8mb4 COLLATE utf8mb4_0900_ai_ci',
  [('id', 'BIGINT UNSIGNED', False),
   ('instance_id', 'BIGINT UNSIGNED', False),
   ('app_id', 'BIGINT UNSIGNED', False),
   ('created_at', 'DATETIME(3)', False)],
  [('idx_database_instance_application_app',
    'CREATE INDEX idx_database_instance_application_app ON database_instance_application (app_id)')],
  [('instance_id', 'app_id')]),
 ('database_instance_host',
  'CREATE TABLE database_instance_host (\n'
  '\tid BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, \n'
  '\tinstance_id BIGINT UNSIGNED NOT NULL, \n'
  '\thost_id BIGINT UNSIGNED NOT NULL, \n'
  '\tcreated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), \n'
  '\tCONSTRAINT pk_database_instance_host PRIMARY KEY (id), \n'
  '\tCONSTRAINT uk_database_instance_host UNIQUE (instance_id, host_id)\n'
  ')CHARSET=utf8mb4 COLLATE utf8mb4_0900_ai_ci',
  [('id', 'BIGINT UNSIGNED', False),
   ('instance_id', 'BIGINT UNSIGNED', False),
   ('host_id', 'BIGINT UNSIGNED', False),
   ('created_at', 'DATETIME(3)', False)],
  [('idx_database_instance_host_host',
    'CREATE INDEX idx_database_instance_host_host ON database_instance_host (host_id)')],
  [('instance_id', 'host_id')]),
 ('database_role_instance',
  'CREATE TABLE database_role_instance (\n'
  '\tid BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, \n'
  '\trole_id BIGINT UNSIGNED NOT NULL, \n'
  '\tinstance_id BIGINT UNSIGNED NOT NULL, \n'
  '\tgranted_by BIGINT UNSIGNED, \n'
  '\tcreated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), \n'
  '\tCONSTRAINT pk_database_role_instance PRIMARY KEY (id), \n'
  '\tCONSTRAINT uk_database_role_instance UNIQUE (role_id, instance_id)\n'
  ')CHARSET=utf8mb4 COLLATE utf8mb4_0900_ai_ci',
  [('id', 'BIGINT UNSIGNED', False),
   ('role_id', 'BIGINT UNSIGNED', False),
   ('instance_id', 'BIGINT UNSIGNED', False),
   ('granted_by', 'BIGINT UNSIGNED', True),
   ('created_at', 'DATETIME(3)', False)],
  [('idx_database_role_instance_instance',
    'CREATE INDEX idx_database_role_instance_instance ON database_role_instance (instance_id)')],
  [('role_id', 'instance_id')]),
 ('database_task',
  'CREATE TABLE database_task (\n'
  '\tid BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, \n'
  '\towner_id BIGINT UNSIGNED NOT NULL, \n'
  '\tactor_name VARCHAR(64) NOT NULL, \n'
  '\tsource_ip VARCHAR(45), \n'
  '\tinstance_id BIGINT UNSIGNED NOT NULL, \n'
  '\tkind VARCHAR(24) NOT NULL, \n'
  '\tstatus VARCHAR(24) NOT NULL, \n'
  '\tengine VARCHAR(16) NOT NULL, \n'
  '\tcontext JSON NOT NULL, \n'
  '\ttarget_snapshot JSON NOT NULL, \n'
  '\tcredential_snapshot JSON NOT NULL, \n'
  '\tsubmitted_text_enc MEDIUMTEXT, \n'
  '\texecuted_statement_enc MEDIUMTEXT, \n'
  '\tsubmitted_bytes BIGINT NOT NULL, \n'
  '\tsubmitted_sha256 VARCHAR(64) NOT NULL, \n'
  '\toperation_summary VARCHAR(512) NOT NULL, \n'
  '\tprepared_operation_enc MEDIUMTEXT, \n'
  '\tpolicy_snapshot JSON, \n'
  '\tconfig_revision INTEGER NOT NULL, \n'
  '\tcredential_id BIGINT UNSIGNED NOT NULL, \n'
  '\tcredential_updated_at DATETIME(3) NOT NULL, \n'
  '\tqueue_deadline_at DATETIME(3), \n'
  '\tstarted_at DATETIME(3), \n'
  '\tdeadline_at DATETIME(3), \n'
  '\ttarget_attempted_at DATETIME(3), \n'
  '\tfinished_at DATETIME(3), \n'
  '\texecutor_id VARCHAR(64), \n'
  '\tlease_token VARCHAR(64), \n'
  '\tlease_expires_at DATETIME(3), \n'
  '\theartbeat_at DATETIME(3), \n'
  '\tbackend_ref JSON, \n'
  '\tcancel_requested_at DATETIME(3), \n'
  '\tstop_reason VARCHAR(64), \n'
  '\terror_code VARCHAR(64), \n'
  '\terror_message VARCHAR(512), \n'
  '\tstop_confirmation JSON, \n'
  '\tquota_snapshot JSON, \n'
  '\tquota_released BOOL NOT NULL, \n'
  '\tresult_reserved_bytes BIGINT NOT NULL, \n'
  '\tresult_summary JSON, \n'
  '\tcreated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), \n'
  '\tupdated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3), \n'
  '\tCONSTRAINT pk_database_task PRIMARY KEY (id)\n'
  ')CHARSET=utf8mb4 COLLATE utf8mb4_0900_ai_ci',
  [('id', 'BIGINT UNSIGNED', False),
   ('owner_id', 'BIGINT UNSIGNED', False),
   ('actor_name', 'VARCHAR(64)', False),
   ('source_ip', 'VARCHAR(45)', True),
   ('instance_id', 'BIGINT UNSIGNED', False),
   ('kind', 'VARCHAR(24)', False),
   ('status', 'VARCHAR(24)', False),
   ('engine', 'VARCHAR(16)', False),
   ('context', 'JSON', False),
   ('target_snapshot', 'JSON', False),
   ('credential_snapshot', 'JSON', False),
   ('submitted_text_enc', 'MEDIUMTEXT', True),
   ('executed_statement_enc', 'MEDIUMTEXT', True),
   ('submitted_bytes', 'BIGINT', False),
   ('submitted_sha256', 'VARCHAR(64)', False),
   ('operation_summary', 'VARCHAR(512)', False),
   ('prepared_operation_enc', 'MEDIUMTEXT', True),
   ('policy_snapshot', 'JSON', True),
   ('config_revision', 'INTEGER', False),
   ('credential_id', 'BIGINT UNSIGNED', False),
   ('credential_updated_at', 'DATETIME(3)', False),
   ('queue_deadline_at', 'DATETIME(3)', True),
   ('started_at', 'DATETIME(3)', True),
   ('deadline_at', 'DATETIME(3)', True),
   ('target_attempted_at', 'DATETIME(3)', True),
   ('finished_at', 'DATETIME(3)', True),
   ('executor_id', 'VARCHAR(64)', True),
   ('lease_token', 'VARCHAR(64)', True),
   ('lease_expires_at', 'DATETIME(3)', True),
   ('heartbeat_at', 'DATETIME(3)', True),
   ('backend_ref', 'JSON', True),
   ('cancel_requested_at', 'DATETIME(3)', True),
   ('stop_reason', 'VARCHAR(64)', True),
   ('error_code', 'VARCHAR(64)', True),
   ('error_message', 'VARCHAR(512)', True),
   ('stop_confirmation', 'JSON', True),
   ('quota_snapshot', 'JSON', True),
   ('quota_released', 'BOOL', False),
   ('result_reserved_bytes', 'BIGINT', False),
   ('result_summary', 'JSON', True),
   ('created_at', 'DATETIME(3)', False),
   ('updated_at', 'DATETIME(3)', False)],
  [('idx_database_task_claim', 'CREATE INDEX idx_database_task_claim ON database_task (status, created_at)'),
   ('idx_database_task_finished', 'CREATE INDEX idx_database_task_finished ON database_task (finished_at)'),
   ('idx_database_task_instance',
    'CREATE INDEX idx_database_task_instance ON database_task (instance_id, status)'),
   ('idx_database_task_lease', 'CREATE INDEX idx_database_task_lease ON database_task (lease_expires_at)'),
   ('idx_database_task_owner',
    'CREATE INDEX idx_database_task_owner ON database_task (owner_id, created_at)')],
  []),
 ('database_result',
  'CREATE TABLE database_result (\n'
  '\tid BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, \n'
  '\ttask_id BIGINT UNSIGNED NOT NULL, \n'
  '\towner_id BIGINT UNSIGNED NOT NULL, \n'
  '\tinstance_id BIGINT UNSIGNED NOT NULL, \n'
  '\tready BOOL NOT NULL, \n'
  '\tmanifest_enc MEDIUMTEXT NOT NULL, \n'
  '\treturned_rows INTEGER NOT NULL, \n'
  '\treturned_bytes BIGINT NOT NULL, \n'
  '\tstored_bytes BIGINT NOT NULL, \n'
  '\treserved_bytes BIGINT NOT NULL, \n'
  '\ttruncated BOOL NOT NULL, \n'
  '\ttruncate_reason VARCHAR(64), \n'
  '\tfinished_at DATETIME(3), \n'
  '\texpires_at DATETIME(3), \n'
  '\tstorage_released BOOL NOT NULL, \n'
  '\tcreated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), \n'
  '\tupdated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3), \n'
  '\tCONSTRAINT pk_database_result PRIMARY KEY (id), \n'
  '\tCONSTRAINT uk_database_result_task_id UNIQUE (task_id)\n'
  ')CHARSET=utf8mb4 COLLATE utf8mb4_0900_ai_ci',
  [('id', 'BIGINT UNSIGNED', False),
   ('task_id', 'BIGINT UNSIGNED', False),
   ('owner_id', 'BIGINT UNSIGNED', False),
   ('instance_id', 'BIGINT UNSIGNED', False),
   ('ready', 'BOOL', False),
   ('manifest_enc', 'MEDIUMTEXT', False),
   ('returned_rows', 'INTEGER', False),
   ('returned_bytes', 'BIGINT', False),
   ('stored_bytes', 'BIGINT', False),
   ('reserved_bytes', 'BIGINT', False),
   ('truncated', 'BOOL', False),
   ('truncate_reason', 'VARCHAR(64)', True),
   ('finished_at', 'DATETIME(3)', True),
   ('expires_at', 'DATETIME(3)', True),
   ('storage_released', 'BOOL', False),
   ('created_at', 'DATETIME(3)', False),
   ('updated_at', 'DATETIME(3)', False)],
  [('idx_database_result_expiry', 'CREATE INDEX idx_database_result_expiry ON database_result (expires_at)'),
   ('idx_database_result_owner',
    'CREATE INDEX idx_database_result_owner ON database_result (owner_id, ready)')],
  [('task_id',)]),
 ('database_result_chunk',
  'CREATE TABLE database_result_chunk (\n'
  '\tid BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, \n'
  '\tresult_id BIGINT UNSIGNED NOT NULL, \n'
  '\tchunk_no INTEGER NOT NULL, \n'
  '\tstart_row INTEGER NOT NULL, \n'
  '\trow_count INTEGER NOT NULL, \n'
  '\tpayload_enc MEDIUMTEXT NOT NULL, \n'
  '\tstored_bytes BIGINT NOT NULL, \n'
  '\tcreated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), \n'
  '\tCONSTRAINT pk_database_result_chunk PRIMARY KEY (id), \n'
  '\tCONSTRAINT uk_database_result_chunk UNIQUE (result_id, chunk_no)\n'
  ')CHARSET=utf8mb4 COLLATE utf8mb4_0900_ai_ci',
  [('id', 'BIGINT UNSIGNED', False),
   ('result_id', 'BIGINT UNSIGNED', False),
   ('chunk_no', 'INTEGER', False),
   ('start_row', 'INTEGER', False),
   ('row_count', 'INTEGER', False),
   ('payload_enc', 'MEDIUMTEXT', False),
   ('stored_bytes', 'BIGINT', False),
   ('created_at', 'DATETIME(3)', False)],
  [('idx_database_result_chunk_rows',
    'CREATE INDEX idx_database_result_chunk_rows ON database_result_chunk (result_id, start_row)')],
  [('result_id', 'chunk_no')]),
 ('database_audit_event',
  'CREATE TABLE database_audit_event (\n'
  '\tid BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, \n'
  '\ttask_id BIGINT UNSIGNED NOT NULL, \n'
  '\tevent_type VARCHAR(32) NOT NULL, \n'
  '\tactor_id BIGINT UNSIGNED, \n'
  '\tactor_name VARCHAR(64), \n'
  '\tsource_ip VARCHAR(45), \n'
  '\tinstance_id BIGINT UNSIGNED, \n'
  '\tinstance_name VARCHAR(128), \n'
  '\tengine VARCHAR(16), \n'
  '\ttarget_snapshot JSON NOT NULL, \n'
  '\tcredential_snapshot JSON NOT NULL, \n'
  '\tcontext JSON NOT NULL, \n'
  '\tsubmitted_text_enc MEDIUMTEXT, \n'
  '\texecuted_statement_enc MEDIUMTEXT, \n'
  '\tsubmitted_bytes BIGINT, \n'
  '\tsubmitted_sha256 VARCHAR(64), \n'
  '\tvalidation JSON, \n'
  '\tstop_confirmation JSON, \n'
  '\tresult_summary JSON, \n'
  '\terror_code VARCHAR(64), \n'
  '\terror_message VARCHAR(512), \n'
  '\tterminal_at DATETIME(3), \n'
  '\tcreated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), \n'
  '\tCONSTRAINT pk_database_audit_event PRIMARY KEY (id)\n'
  ')CHARSET=utf8mb4 COLLATE utf8mb4_0900_ai_ci',
  [('id', 'BIGINT UNSIGNED', False),
   ('task_id', 'BIGINT UNSIGNED', False),
   ('event_type', 'VARCHAR(32)', False),
   ('actor_id', 'BIGINT UNSIGNED', True),
   ('actor_name', 'VARCHAR(64)', True),
   ('source_ip', 'VARCHAR(45)', True),
   ('instance_id', 'BIGINT UNSIGNED', True),
   ('instance_name', 'VARCHAR(128)', True),
   ('engine', 'VARCHAR(16)', True),
   ('target_snapshot', 'JSON', False),
   ('credential_snapshot', 'JSON', False),
   ('context', 'JSON', False),
   ('submitted_text_enc', 'MEDIUMTEXT', True),
   ('executed_statement_enc', 'MEDIUMTEXT', True),
   ('submitted_bytes', 'BIGINT', True),
   ('submitted_sha256', 'VARCHAR(64)', True),
   ('validation', 'JSON', True),
   ('stop_confirmation', 'JSON', True),
   ('result_summary', 'JSON', True),
   ('error_code', 'VARCHAR(64)', True),
   ('error_message', 'VARCHAR(512)', True),
   ('terminal_at', 'DATETIME(3)', True),
   ('created_at', 'DATETIME(3)', False)],
  [('idx_database_audit_actor',
    'CREATE INDEX idx_database_audit_actor ON database_audit_event (actor_id, created_at)'),
   ('idx_database_audit_instance',
    'CREATE INDEX idx_database_audit_instance ON database_audit_event (instance_id, created_at)'),
   ('idx_database_audit_task', 'CREATE INDEX idx_database_audit_task ON database_audit_event (task_id, id)'),
   ('idx_database_audit_terminal',
    'CREATE INDEX idx_database_audit_terminal ON database_audit_event (terminal_at)'),
   ('idx_database_audit_type',
    'CREATE INDEX idx_database_audit_type ON database_audit_event (task_id, event_type)')],
  []),
 ('database_quota_bucket',
  'CREATE TABLE database_quota_bucket (\n'
  '\tid BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, \n'
  '\tscope VARCHAR(16) NOT NULL, \n'
  '\tscope_key VARCHAR(128) NOT NULL, \n'
  '\tactive_count INTEGER NOT NULL, \n'
  '\tdata_active_count INTEGER NOT NULL, \n'
  '\tqueued_count INTEGER NOT NULL, \n'
  '\treserved_bytes BIGINT NOT NULL, \n'
  '\tpayload_bytes BIGINT NOT NULL, \n'
  '\ttarget_sockets BIGINT NOT NULL, \n'
  '\tcontrol_sockets BIGINT NOT NULL, \n'
  '\tcontrol_operations INTEGER NOT NULL, \n'
  '\towner VARCHAR(64), \n'
  '\ttoken VARCHAR(64), \n'
  '\tlease_expires_at DATETIME(3), \n'
  '\theartbeat_at DATETIME(3), \n'
  '\tcreated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), \n'
  '\tupdated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3), \n'
  '\tCONSTRAINT pk_database_quota_bucket PRIMARY KEY (id), \n'
  '\tCONSTRAINT uk_database_quota_bucket UNIQUE (scope, scope_key)\n'
  ')CHARSET=utf8mb4 COLLATE utf8mb4_0900_ai_ci',
  [('id', 'BIGINT UNSIGNED', False),
   ('scope', 'VARCHAR(16)', False),
   ('scope_key', 'VARCHAR(128)', False),
   ('active_count', 'INTEGER', False),
   ('data_active_count', 'INTEGER', False),
   ('queued_count', 'INTEGER', False),
   ('reserved_bytes', 'BIGINT', False),
   ('payload_bytes', 'BIGINT', False),
   ('target_sockets', 'BIGINT', False),
   ('control_sockets', 'BIGINT', False),
   ('control_operations', 'INTEGER', False),
   ('owner', 'VARCHAR(64)', True),
   ('token', 'VARCHAR(64)', True),
   ('lease_expires_at', 'DATETIME(3)', True),
   ('heartbeat_at', 'DATETIME(3)', True),
   ('created_at', 'DATETIME(3)', False),
   ('updated_at', 'DATETIME(3)', False)],
  [],
  [('scope', 'scope_key')])]


def _type_name(value):
    value = str(value).upper().replace("INTEGER", "INT").replace("BOOL", "TINYINT(1)")
    return value.replace(" ", "")


def upgrade():
    bind = op.get_bind()
    for name, ddl, columns, indexes, unique in TABLES:
        inspector = sa.inspect(bind)
        if not inspector.has_table(name):
            op.execute(sa.text(ddl))
        else:
            actual = {c["name"]: c for c in inspector.get_columns(name)}
            for column, expected_type, nullable in columns:
                if column not in actual:
                    raise RuntimeError(f"{name}.{column} 缺失，拒绝跳过不完整表")
                found = actual[column]
                if _type_name(found["type"].compile(dialect=bind.dialect)) != _type_name(expected_type) or found["nullable"] != nullable:
                    raise RuntimeError(f"{name}.{column} 结构与数据库管理迁移合同不一致")
            actual_unique = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(name)}
            if any(tuple(keys) not in actual_unique for keys in unique):
                raise RuntimeError(f"{name} 缺少必要唯一约束")
            if inspector.get_pk_constraint(name)["constrained_columns"] != ["id"]:
                raise RuntimeError(f"{name} 主键结构不一致")
        existing = {index["name"]: index["column_names"] for index in sa.inspect(bind).get_indexes(name)}
        for index_name, statement in indexes:
            if index_name not in existing:
                op.execute(sa.text(statement))
            else:
                expected = [key.strip().strip("`") for key in statement.rsplit("(", 1)[1].rstrip(")").split(",")]
                if existing[index_name] != expected:
                    raise RuntimeError(f"{name}.{index_name} 索引列不一致")
    # 首次并发前建立唯一全局锁行；后续桶由持有该锁的短事务串行创建。
    op.execute(sa.text("""INSERT IGNORE INTO database_quota_bucket
        (scope,scope_key,active_count,data_active_count,queued_count,reserved_bytes,payload_bytes,
         target_sockets,control_sockets,control_operations)
        VALUES ('global','all',0,0,0,0,0,0,0,0)"""))


def downgrade():
    for name, *_ in reversed(TABLES):
        if sa.inspect(op.get_bind()).has_table(name):
            op.drop_table(name)
