"""DB-031：notification_preferences 表（每家長每事件唯一、事件值域、cascade、預設值）。"""

from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    SEEDED_UPDATED_AT,
    UNIQUE_VIOLATION,
    Conn,
    assert_backend_grants,
    assert_backend_read_write,
    assert_updated_at_trigger,
    pg_error,
)
from tests.integration.db.factories import make_notification_preferences, make_parent_accounts

PARENT_LINE_EVENTS = (
    "attendance.checked_in",
    "attendance.checked_out",
    "homework.eta_updated",
    "homework.done",
    "pickup.replied",
    "pickup.completed",
    "exam.published",
)


def test_notification_preferences_unique(backend_conn: Conn) -> None:
    first = make_notification_preferences(backend_conn, event="homework.done")

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_notification_preferences(
            backend_conn, parent_account_id=first["parent_account_id"], event="homework.done"
        )
    assert err.constraint_name == "uq_notification_preferences_parent_event"

    other_event = make_notification_preferences(
        backend_conn, parent_account_id=first["parent_account_id"], event="exam.published"
    )
    assert other_event["event"] == "exam.published"


def test_notification_preferences_event_domain(backend_conn: Conn) -> None:
    for bad_event in ("pickup.requested", "binding.completed", "leave.created"):
        with pg_error(backend_conn, CHECK_VIOLATION) as err:
            make_notification_preferences(backend_conn, event=bad_event)
        assert err.constraint_name == "ck_notification_preferences_event"

    parent = make_parent_accounts(backend_conn)
    for event in PARENT_LINE_EVENTS:
        row = make_notification_preferences(
            backend_conn, parent_account_id=parent["id"], event=event
        )
        assert row["event"] == event


def test_notification_preferences_default_enabled(backend_conn: Conn) -> None:
    assert make_notification_preferences(backend_conn)["line_enabled"] is True


def test_notification_preferences_cascade(backend_conn: Conn) -> None:
    row = make_notification_preferences(backend_conn)

    backend_conn.execute(
        "delete from public.parent_accounts where id = %s", (row["parent_account_id"],)
    )

    remaining = backend_conn.execute(
        "select count(*) from public.notification_preferences where id = %s", (row["id"],)
    ).fetchone()
    assert remaining == (0,)


def test_notification_preferences_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.notification_preferences")

    row = make_notification_preferences(backend_conn)
    assert_backend_read_write(
        backend_conn, "public.notification_preferences", row, line_enabled=False
    )


def test_notification_preferences_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_notification_preferences(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(
        backend_conn, "public.notification_preferences", row["id"], line_enabled=False
    )
