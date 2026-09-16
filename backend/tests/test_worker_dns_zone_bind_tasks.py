"""DNS Zone 异步绑定任务的 Worker 接线测试。"""
from app import worker_main


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return None


async def test_worker_processes_one_zone_bind_task_with_own_session(monkeypatch):
    session = object()
    processed_sessions = []

    async def fake_process_next_zone_bind_task(current_session):
        processed_sessions.append(current_session)
        return True

    monkeypatch.setattr(worker_main, "async_session_factory", lambda: _SessionContext(session))
    monkeypatch.setattr(
        worker_main.domain_bind_task_service,
        "process_next_zone_bind_task",
        fake_process_next_zone_bind_task,
    )

    assert await worker_main._process_next_zone_bind_task() is True
    assert processed_sessions == [session]
