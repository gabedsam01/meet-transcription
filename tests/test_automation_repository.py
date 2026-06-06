from datetime import datetime, timedelta, timezone

from app.repositories.memory import build_memory_repositories


def _now():
    return datetime.now(timezone.utc)


def test_get_for_user_is_none_initially():
    repos = build_memory_repositories()
    assert repos.automation.get_for_user(7) is None


def test_upsert_creates_then_updates_in_place():
    repos = build_memory_repositories()
    created = repos.automation.upsert_for_user(
        7, auto_poll_enabled=True, poll_interval_seconds=120, max_files_per_poll=3
    )
    assert created.user_id == 7
    assert created.auto_poll_enabled is True
    assert created.poll_interval_seconds == 120
    assert created.max_files_per_poll == 3

    updated = repos.automation.upsert_for_user(7, poll_interval_seconds=600)
    assert updated.poll_interval_seconds == 600
    assert updated.auto_poll_enabled is True  # untouched field preserved
    # No duplicate row.
    assert repos.automation.get_for_user(7).poll_interval_seconds == 600


def test_list_due_for_poll_only_enabled_and_due():
    repos = build_memory_repositories()
    now = _now()
    # enabled + never polled -> due
    repos.automation.upsert_for_user(1, auto_poll_enabled=True, poll_interval_seconds=300)
    # enabled + polled long ago -> due
    repos.automation.upsert_for_user(2, auto_poll_enabled=True, poll_interval_seconds=300)
    repos.automation.mark_poll_result(2, now - timedelta(seconds=400), success=True)
    # enabled + polled recently -> NOT due
    repos.automation.upsert_for_user(3, auto_poll_enabled=True, poll_interval_seconds=300)
    repos.automation.mark_poll_result(3, now - timedelta(seconds=10), success=True)
    # disabled -> never due
    repos.automation.upsert_for_user(4, auto_poll_enabled=False, poll_interval_seconds=1)

    due = {s.user_id for s in repos.automation.list_due_for_poll(now, limit=10)}
    assert due == {1, 2}


def test_list_due_for_poll_respects_limit():
    repos = build_memory_repositories()
    now = _now()
    for uid in range(1, 6):
        repos.automation.upsert_for_user(uid, auto_poll_enabled=True, poll_interval_seconds=300)
    assert len(repos.automation.list_due_for_poll(now, limit=2)) == 2


def test_mark_poll_result_success_and_failure():
    repos = build_memory_repositories()
    now = _now()
    repos.automation.upsert_for_user(7, auto_poll_enabled=True)
    repos.automation.mark_poll_result(7, now, success=True)
    s = repos.automation.get_for_user(7)
    assert s.last_poll_at == now and s.last_success_at == now
    assert s.last_error_code is None and s.last_error_message is None

    later = now + timedelta(seconds=300)
    repos.automation.mark_poll_result(
        7, later, success=False, error_code="DRIVE_ERROR", error_message="pasta inválida"
    )
    s = repos.automation.get_for_user(7)
    assert s.last_poll_at == later
    assert s.last_success_at == now  # unchanged on failure
    assert s.last_error_code == "DRIVE_ERROR"
    assert s.last_error_message == "pasta inválida"
