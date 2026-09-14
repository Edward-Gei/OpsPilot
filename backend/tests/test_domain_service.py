"""DNS Zone 服务层：绑定、快照与同步租约。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.constants import DomainProvider
from app.core.response import BizError
from app.core.security import encrypt_text
from app.dns_providers.base import ProviderUnavailableError, RemoteRecordSet, ZoneRef
from app.models.domain import DnsRecordSet, DnsZone
from app.models.job import Credential
from app.schemas.domain import ZoneBindSelection
from app.services import domain_service


AUDIT_CONTEXT = domain_service.DomainAuditContext(1, "admin", "127.0.0.1")
REMOTE_A = RemoteRecordSet(
    "a-record", "api.example.com", "A", 300, ("192.0.2.1",), None,
    {"locator": {"name": "api.example.com", "type": "A"}},
)
REMOTE_TXT = RemoteRecordSet(
    "txt-record", "example.com", "TXT", 300, ("v=spf1 -all",), None,
    {"locator": {"name": "example.com", "type": "TXT"}},
)


class FakeAdapter:
    """只实现服务层测试需要的远端发现和快照读取。"""

    def __init__(self, *, records=(), list_error: Exception | None = None):
        self.records = list(records)
        self.list_error = list_error
        self.credentials = []

    async def discover_public_zones(self, credential):
        self.credentials.append(credential)
        return [ZoneRef(DomainProvider.AWS_ROUTE53, "Z1", "example.com")]

    async def list_record_sets(self, zone, credential):
        if self.list_error:
            raise self.list_error
        return self.records


async def _create_cloud_credential(session, name: str, auth_type: str) -> Credential:
    credential = Credential(
        name=name,
        login_user="access-key" if auth_type == "username_password" else None,
        auth_type=auth_type,
        secret_enc=encrypt_text("provider-secret"),
    )
    session.add(credential)
    await session.flush()
    return credential


async def _seed_zone_with_record(db_factory, *, operation_expires_at=None) -> DnsZone:
    async with db_factory() as session:
        credential = await _create_cloud_credential(session, "route53-prod", "username_password")
        zone = DnsZone(
            provider=DomainProvider.AWS_ROUTE53.value,
            remote_zone_id="Z1",
            zone_name="example.com",
            credential_id=credential.id,
            record_count=1,
            operation_token="active" if operation_expires_at else None,
            operation_kind="sync" if operation_expires_at else None,
            operation_expires_at=operation_expires_at,
        )
        session.add(zone)
        await session.flush()
        session.add(
            DnsRecordSet(
                zone_id=zone.id,
                record_key=REMOTE_A.record_key,
                record_name=REMOTE_A.record_name,
                record_type=REMOTE_A.record_type,
                ttl=REMOTE_A.ttl,
                values=list(REMOTE_A.values),
                provider_meta=REMOTE_A.provider_meta,
            )
        )
        await session.commit()
        return zone


@pytest.mark.asyncio
async def test_initial_bind_persists_zone_and_records_only_after_full_sync(db_factory, monkeypatch):
    adapter = FakeAdapter(records=[REMOTE_A, REMOTE_TXT])
    monkeypatch.setattr(domain_service, "get_adapter", lambda provider: adapter)
    async with db_factory() as session:
        credential = await _create_cloud_credential(session, "route53-prod", "username_password")

        result = await domain_service.bind_zones(
            session,
            provider=DomainProvider.AWS_ROUTE53,
            credential_id=credential.id,
            selections=[ZoneBindSelection(remote_zone_id="Z1", description="生产")],
            created_by=1,
            audit_context=AUDIT_CONTEXT,
        )

        assert result[0].status == "success"
        assert (await session.execute(select(func.count()).select_from(DnsZone))).scalar_one() == 1
        assert (await session.execute(select(func.count()).select_from(DnsRecordSet))).scalar_one() == 2


@pytest.mark.asyncio
async def test_initial_bind_failure_leaves_no_zone_or_snapshot(db_factory, monkeypatch):
    monkeypatch.setattr(
        domain_service,
        "get_adapter",
        lambda provider: FakeAdapter(list_error=ProviderUnavailableError("x")),
    )
    async with db_factory() as session:
        credential = await _create_cloud_credential(session, "route53-prod", "username_password")

        result = await domain_service.bind_zones(
            session,
            provider=DomainProvider.AWS_ROUTE53,
            credential_id=credential.id,
            selections=[ZoneBindSelection(remote_zone_id="Z1", description=None)],
            created_by=1,
            audit_context=AUDIT_CONTEXT,
        )

        assert result[0].status == "failed"
        assert (await session.execute(select(func.count()).select_from(DnsZone))).scalar_one() == 0
        assert (await session.execute(select(func.count()).select_from(DnsRecordSet))).scalar_one() == 0


@pytest.mark.asyncio
async def test_sync_rejects_active_lease_and_preserves_snapshot_on_read_failure(db_factory):
    zone = await _seed_zone_with_record(
        db_factory,
        operation_expires_at=datetime.now() + timedelta(minutes=2),
    )
    async with db_factory() as session:
        with pytest.raises(BizError) as exc:
            await domain_service.sync_zone(session, zone.id, AUDIT_CONTEXT)

    assert exc.value.code == 40901


@pytest.mark.asyncio
async def test_list_compatible_credentials_returns_safe_provider_metadata(db_factory):
    async with db_factory() as session:
        aws = await _create_cloud_credential(session, "route53-prod", "username_password")
        await _create_cloud_credential(session, "ssh-prod", "password")
        await _create_cloud_credential(session, "gcp-prod", "secret_file")
        aws.description = "Route 53 生产凭据"
        await session.commit()

        items = await domain_service.list_compatible_credentials(session, DomainProvider.AWS_ROUTE53)

    assert items == [{
        "id": aws.id,
        "name": "route53-prod",
        "auth_type": "username_password",
        "description": "Route 53 生产凭据",
    }]


@pytest.mark.asyncio
async def test_discover_uses_decrypted_credential_without_auditing_secret(db_factory, monkeypatch):
    adapter = FakeAdapter()
    audit_events = []
    monkeypatch.setattr(domain_service, "get_adapter", lambda provider: adapter)
    monkeypatch.setattr(domain_service.audit, "log", lambda **event: audit_events.append(event))
    async with db_factory() as session:
        credential = await _create_cloud_credential(session, "route53-prod", "username_password")

        zones = await domain_service.discover_zones(
            session,
            DomainProvider.AWS_ROUTE53,
            credential.id,
            AUDIT_CONTEXT,
        )

    assert zones == [ZoneRef(DomainProvider.AWS_ROUTE53, "Z1", "example.com")]
    assert adapter.credentials[0].secret == "provider-secret"
    assert all("provider-secret" not in repr(event) for event in audit_events)


@pytest.mark.asyncio
async def test_failed_sync_keeps_old_records_and_sets_sanitized_status(db_factory, monkeypatch):
    zone = await _seed_zone_with_record(db_factory)
    monkeypatch.setattr(
        domain_service,
        "get_adapter",
        lambda provider: FakeAdapter(list_error=ProviderUnavailableError("raw secret must not escape")),
    )
    async with db_factory() as session:
        with pytest.raises(BizError) as exc:
            await domain_service.sync_zone(session, zone.id, AUDIT_CONTEXT)
        record = (await session.execute(
            select(DnsRecordSet).where(DnsRecordSet.zone_id == zone.id)
        )).scalar_one()
        refreshed_zone = await session.get(DnsZone, zone.id)

    assert exc.value.code == 50201
    assert record.values == ["192.0.2.1"]
    assert refreshed_zone.sync_status == "failed"
    assert "secret" not in (refreshed_zone.last_sync_error or "").lower()


@pytest.mark.asyncio
async def test_successful_sync_replaces_snapshot_and_releases_lease(db_factory, monkeypatch):
    zone = await _seed_zone_with_record(db_factory)
    monkeypatch.setattr(domain_service, "get_adapter", lambda provider: FakeAdapter(records=[REMOTE_TXT]))
    async with db_factory() as session:
        refreshed = await domain_service.sync_zone(session, zone.id, AUDIT_CONTEXT)
        records = list((await session.execute(
            select(DnsRecordSet).where(DnsRecordSet.zone_id == zone.id)
        )).scalars())

    assert refreshed.record_count == 1
    assert refreshed.sync_status == "success"
    assert refreshed.operation_token is None
    assert [record.record_key for record in records] == [REMOTE_TXT.record_key]


@pytest.mark.asyncio
async def test_discover_rejects_incompatible_credential_before_provider_call(db_factory, monkeypatch):
    adapter_called = False

    def adapter_factory(provider):
        nonlocal adapter_called
        adapter_called = True
        return FakeAdapter()

    monkeypatch.setattr(domain_service, "get_adapter", adapter_factory)
    async with db_factory() as session:
        credential = await _create_cloud_credential(session, "gcp-prod", "secret_file")

        with pytest.raises(BizError) as exc:
            await domain_service.discover_zones(
                session,
                DomainProvider.AWS_ROUTE53,
                credential.id,
                AUDIT_CONTEXT,
            )

    assert exc.value.code == 42201
    assert adapter_called is False


@pytest.mark.asyncio
async def test_update_zone_description_changes_only_local_metadata(db_factory):
    zone = await _seed_zone_with_record(db_factory)
    async with db_factory() as session:
        updated = await domain_service.update_zone_description(
            session,
            zone.id,
            "新的本地说明",
            AUDIT_CONTEXT,
        )

    assert updated.description == "新的本地说明"
    assert updated.record_count == 1


@pytest.mark.asyncio
async def test_unbind_deletes_only_local_zone_and_snapshot(db_factory):
    zone = await _seed_zone_with_record(db_factory)
    async with db_factory() as session:
        await domain_service.unbind_zone(session, zone.id, AUDIT_CONTEXT)

        zone_count = (await session.execute(select(func.count()).select_from(DnsZone))).scalar_one()
        record_count = (await session.execute(select(func.count()).select_from(DnsRecordSet))).scalar_one()

    assert zone_count == 0
    assert record_count == 0


@pytest.mark.asyncio
async def test_zone_and_record_lists_apply_local_filters(db_factory):
    zone = await _seed_zone_with_record(db_factory)
    async with db_factory() as session:
        session.add(DnsZone(
            provider=DomainProvider.GOOGLE_CLOUD_DNS.value,
            remote_zone_id="gcp-zone",
            zone_name="other.example.com",
            credential_id=1,
            description="其他 Zone",
            sync_status="failed",
        ))
        await session.commit()

        zones, zone_total = await domain_service.list_zones(
            session,
            page=1,
            page_size=20,
            keyword="example",
            provider=DomainProvider.AWS_ROUTE53,
            sync_status="success",
        )
        records, record_total = await domain_service.list_record_sets(
            session,
            zone.id,
            page=1,
            page_size=20,
            keyword="api",
            record_type="A",
            read_only=False,
        )

    assert zone_total == 1
    assert [item.id for item in zones] == [zone.id]
    assert record_total == 1
    assert [item.record_name for item in records] == ["api.example.com"]
