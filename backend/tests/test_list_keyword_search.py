"""列表关键词按可见业务列检索，且在分页前计算总数。"""

from datetime import datetime

from app.models.application_config import ConfigFile, ConfigPlatformInstance, ConfigVersion
from app.models.auth import Role, User
from app.models.cmdb import JobHost
from app.models.domain import DnsRecordSet, DnsZone
from app.models.job import Credential, ProcessStep, ProcessTemplate, TicketTemplate
from app.models.ticket import Ticket
from app.models.audit import AuditLog
from app.services import (
    application_config_service, audit_service, credential_service, domain_service,
    job_host_service, rbac_service, search_service, template_service, ticket_service,
)
from app.services.domain_excel import load_zone_export_rows
from tests.conftest import auth_header, login_for_tokens


async def test_credential_search_visible_metadata_but_not_secret(db_factory):
    async with db_factory() as session:
        session.add(Credential(name="ssh-login", login_user="root", auth_type="private_key",
                               secret_enc="hidden-token-734", passphrase_enc="cipher",
                               file_name="deploy.key", description="生产连接"))
        await session.commit()
        for keyword in ("SSH 私钥", "deploy.key", "生产连接", "有"):
            rows, total = await credential_service.list_credentials(
                session, page=1, page_size=1, keyword=keyword, auth_types={"private_key"})
            assert total == 1 and rows[0].name == "ssh-login", keyword
        _, total = await credential_service.list_credentials(
            session, page=1, page_size=1, keyword="hidden-token-734", auth_types={"private_key"})
        assert total == 0


async def test_credential_search_counts_all_matching_pages(db_factory):
    async with db_factory() as session:
        session.add_all([
            Credential(name=f"key-{n}", auth_type="private_key", secret_enc="cipher")
            for n in range(2)
        ])
        await session.commit()
        first, first_total = await credential_service.list_credentials(
            session, page=1, page_size=1, keyword="SSH 私钥")
        second, second_total = await credential_service.list_credentials(
            session, page=2, page_size=1, keyword="SSH 私钥")
        assert first_total == second_total == 2
        assert first[0].id != second[0].id


async def test_job_host_search_association_flags_and_test_result(db_factory):
    async with db_factory() as session:
        credential = Credential(name="deploy-login", auth_type="password", secret_enc="cipher")
        session.add(credential)
        await session.flush()
        session.add(JobHost(name="agent-a", ip="10.0.0.1", ssh_port=2222,
                            credential_id=credential.id, workdir="/srv/deploy",
                            enabled=True, last_check_ok=False,
                            last_check_at=datetime(2026, 10, 24, 12)))
        session.add(JobHost(name="agent-b", ip="10.0.0.3", ssh_port=22,
                            enabled=False, last_check_ok=True))
        await session.commit()
        for keyword in ("deploy-login", "2222", "/srv/deploy", "启用", "失败"):
            _, total = await job_host_service.list_job_hosts(session, page=1, page_size=1, keyword=keyword)
            assert total == 1, keyword
        _, total = await job_host_service.list_job_hosts(session, page=1, page_size=1, keyword="2026")
        assert total == 0
        _, total = await job_host_service.list_job_hosts(session, page=1, page_size=1, keyword="成功")
        assert total == 0


async def test_user_search_roles_source_status_and_mfa(db_factory, seed):
    async with db_factory() as session:
        user = await session.get(User, seed["users"]["admin"])
        user.source = "ldap"
        user.status = "disabled"
        user.mfa_enabled = True
        await session.commit()
        role = await session.get(Role, seed["roles"]["admin"])
        for keyword in (role.name, "LDAP", "禁用", "已开启"):
            rows, total = await rbac_service.list_users(session, page=1, page_size=20, keyword=keyword)
            assert total >= 1 and user.id in [row.id for row in rows], keyword
        rows, _ = await rbac_service.list_users(session, page=1, page_size=20, keyword="未绑定")
        assert user.id not in [row.id for row in rows]


async def test_process_template_search_counts_strategy_and_status(db_factory):
    async with db_factory() as session:
        process = ProcessTemplate(name="deploy-flow", status="disabled",
                                  exec_strategy={"timeout": 712, "fail_fast": True, "kill_on_stop": False})
        session.add(process)
        await session.flush()
        session.add(ProcessStep(process_template_id=process.id, step_order=1, name="build",
                                script_type="shell", content="echo ok"))
        session.add(TicketTemplate(name="entry-a", type="release", job_host_id=1,
                                   process_template_id=process.id))
        await session.commit()
        for keyword in ("1", "712秒 / 失败即停", "终止继续", "停用"):
            _, total = await template_service.list_process_templates(session, page=1, page_size=1, keyword=keyword)
            assert total == 1, keyword
        _, total = await template_service.list_process_templates(
            session, page=1, page_size=1, keyword="停用", name_only=True)
        assert total == 0


async def test_ticket_template_search_associations_roles_and_name_only(db_factory, seed):
    async with db_factory() as session:
        process = ProcessTemplate(name="linked-flow")
        host = JobHost(name="linked-agent", ip="10.0.0.2")
        session.add_all([process, host])
        await session.flush()
        session.add(TicketTemplate(name="deploy-entry", type="release", job_host_id=host.id,
                                   process_template_id=process.id, status="disabled",
                                   visible_role_ids=[seed["roles"]["approver"]]))
        await session.commit()
        role = await session.get(Role, seed["roles"]["approver"])
        for keyword in ("发布", "linked-agent", "linked-flow", role.name, "停用"):
            _, total = await template_service.list_templates(session, page=1, page_size=1, keyword=keyword)
            assert total == 1, keyword
        _, total = await template_service.list_templates(
            session, page=1, page_size=1, keyword="linked-agent", name_only=True)
        assert total == 0
        session.add(TicketTemplate(name="open-entry", type="other", job_host_id=host.id,
                                   process_template_id=process.id, visible_role_ids=[]))
        session.add(TicketTemplate(name="legacy-open-entry", type="other", job_host_id=host.id,
                                   process_template_id=process.id, visible_role_ids=None))
        await session.commit()
        rows, total = await template_service.list_templates(
            session, page=1, page_size=10, keyword="全部角色")
        assert total == 2 and {row.name for row in rows} == {"open-entry", "legacy-open-entry"}


async def test_ticket_search_snapshot_host_and_display_status(db_factory, seed):
    async with db_factory() as session:
        session.add(Ticket(ticket_no="T123", template_id=1, title="deploy", type="release",
                           job_host_id=1, job_host_snap={"name": "historical-agent"},
                           status="approving", creator_id=seed["users"]["admin"]))
        await session.commit()
        for keyword in ("historical-agent", "审批中"):
            _, total = await ticket_service.list_tickets(session, page=1, page_size=1, keyword=keyword)
            assert total == 1, keyword


async def test_zone_search_visible_columns_matches_export(db_factory):
    async with db_factory() as session:
        credential = Credential(name="zone-login", auth_type="api_token", secret_enc="cipher")
        session.add(credential)
        await session.flush()
        session.add(DnsZone(provider="tencent_dnspod", remote_zone_id="z-1",
                            zone_name="example.test", credential_id=credential.id,
                            record_count=17, sync_status="failed", last_sync_error="provider-error"))
        await session.commit()
        for keyword in ("腾讯云 DNSPod", "zone-login", "17", "同步失败", "provider-error"):
            _, total = await domain_service.list_zones(session, page=1, page_size=1, keyword=keyword)
            exported, _ = await load_zone_export_rows(session, keyword=keyword)
            assert total == len(exported) == 1, keyword


async def test_dns_record_search_value_ttl_and_read_only_reason(db_factory):
    async with db_factory() as session:
        zone = DnsZone(provider="aws_route53", remote_zone_id="z-2",
                       zone_name="record.test", credential_id=1)
        session.add(zone)
        await session.flush()
        session.add(DnsRecordSet(zone_id=zone.id, record_key="txt", record_name="txt.record.test",
                                 record_type="TXT", ttl=420, values=["verification-xyz", "中文验证值"],
                                 read_only=True, read_only_reason="供应商限制"))
        await session.commit()
        for keyword in ("TXT", "420", "verification-xyz", "中文验证值", "只读", "供应商限制"):
            _, total = await domain_service.list_record_sets(session, zone.id, page=1, page_size=1, keyword=keyword)
            assert total == 1, keyword


async def test_config_search_visible_associations_locator_version_and_status(db_factory, seed):
    async with db_factory() as session:
        instance = ConfigPlatformInstance(name="nacos-prod", provider="nacos",
                                          base_url="https://nacos.example", credential_id=1)
        session.add(instance)
        await session.flush()
        session.add(ConfigFile(name="service.config", platform_instance_id=instance.id,
                               locator={"namespace": "", "group": "BLUE", "data_id": "db.data"},
                               locator_key="locator-key", content_format="yaml",
                               approval_role_id=seed["roles"]["approver"],
                               status="active", drift_status="drifted"))
        await session.commit()
        role = await session.get(Role, seed["roles"]["approver"])
        for keyword in ("nacos-prod", "public / BLUE", "BLUE", "yaml", role.name, "0", "未发布", "存在漂移"):
            _, total = await application_config_service.list_config_files(
                session, page=1, page_size=1, keyword=keyword)
            assert total == 1, keyword
        _, hidden_key_total = await application_config_service.list_config_files(
            session, page=1, page_size=1, keyword="group")
        assert hidden_key_total == 0


async def test_config_search_rendered_version_number(db_factory, seed):
    async with db_factory() as session:
        instance = ConfigPlatformInstance(name="nacos-version", provider="nacos",
                                          base_url="https://nacos.example", credential_id=1)
        session.add(instance)
        await session.flush()
        file = ConfigFile(name="base", platform_instance_id=instance.id,
                          locator={"namespace": "prod", "group": "DEFAULT", "data_id": "base"},
                          locator_key="base-key", content_format="text",
                          approval_role_id=seed["roles"]["approver"])
        session.add(file)
        await session.flush()
        version = ConfigVersion(config_file_id=file.id, version_no=37,
                                source="edit", status="published", content_enc="cipher")
        session.add(version)
        await session.flush()
        file.current_version_id = version.id
        await session.commit()
        _, total = await application_config_service.list_config_files(
            session, page=1, page_size=1, keyword="v37")
        assert total == 1


async def test_audit_search_visible_columns_and_export(db_factory):
    async with db_factory() as session:
        session.add(AuditLog(id=1, created_at=datetime(2026, 10, 24, 12),
                             actor_name="operator-a", source_ip="172.16.0.4", module="cmdb",
                             action="host.update", target_type="host", target_id="741",
                             target_name="server-a", result="failed"))
        await session.commit()
        for keyword in ("operator-a", "172.16.0.4", "资产", "host.update", "host", "失败"):
            rows, total = await audit_service.list_logs(session, page=1, page_size=1, keyword=keyword)
            exported = await audit_service.collect_export_rows(session, keyword=keyword)
            assert total == len(rows) == len(exported) == 1, keyword


async def test_audit_search_ignores_target_id_hidden_by_name(db_factory):
    async with db_factory() as session:
        session.add(AuditLog(id=1, created_at=datetime(2026, 10, 24, 12),
                             actor_name="operator", module="cmdb", action="update",
                             target_type="host", target_id="hidden-742",
                             target_name="server-a", result="success"))
        session.add(AuditLog(id=2, created_at=datetime(2026, 10, 24, 13),
                             actor_name="operator", module="cmdb", action="update",
                             target_type="host", target_id="visible-741",
                             result="success"))
        session.add(AuditLog(id=3, created_at=datetime(2026, 10, 24, 14),
                             actor_name="operator", module="cmdb", action="update",
                             target_type="", target_id="hidden-743", result="success"))
        await session.commit()
        _, hidden_total = await audit_service.list_logs(session, page=1, page_size=10, keyword="hidden-742")
        _, blank_type_total = await audit_service.list_logs(session, page=1, page_size=10, keyword="hidden-743")
        rows, visible_total = await audit_service.list_logs(session, page=1, page_size=10, keyword="visible-741")
        assert hidden_total == 0
        assert blank_type_total == 0
        assert visible_total == 1 and rows[0].id == 2


async def test_global_search_keeps_original_ticket_and_template_scope(db_factory, seed):
    async with db_factory() as session:
        session.add(Ticket(ticket_no="T456", template_id=1, title="launch", type="release",
                           job_host_id=1, job_host_snap={"name": "historical-agent"},
                           status="approving", creator_id=seed["users"]["admin"]))
        session.add(TicketTemplate(name="entry", type="release", job_host_id=1,
                                   process_template_id=1, status="disabled"))
        await session.commit()
        admin = await session.get(User, seed["users"]["admin"])
        assert (await search_service.search(session, admin, "审批中"))["tickets"]["total"] == 0
        assert (await search_service.search(session, admin, "停用"))["templates"]["total"] == 0


async def test_ticket_template_list_returns_names_used_for_visible_links(client, db_factory):
    async with db_factory() as session:
        process = ProcessTemplate(name="remote-process")
        host = JobHost(name="remote-host", ip="10.0.0.8")
        session.add_all([process, host])
        await session.flush()
        session.add(TicketTemplate(name="linked-entry", type="release",
                                   job_host_id=host.id, process_template_id=process.id))
        await session.commit()
    headers = auth_header(await login_for_tokens(client, "admin"))
    response = await client.get("/api/v1/templates", params={"keyword": "remote-host"}, headers=headers)
    item = response.json()["data"]["items"][0]
    assert item["job_host_name"] == "remote-host"
    assert item["process_template_name"] == "remote-process"
