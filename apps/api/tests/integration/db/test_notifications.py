"""DB-029：notifications 表（recipient_type、event、payload、長度、未讀索引、grant、trigger）。"""

from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from app.notifications.events import Event
from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    SEEDED_UPDATED_AT,
    Conn,
    assert_backend_grants,
    assert_backend_read_write,
    assert_updated_at_trigger,
    pg_error,
)
from tests.integration.db.factories import make_notifications

READ_AT = datetime(2026, 10, 2, 17, 30, tzinfo=timezone(timedelta(hours=8)))


def test_notifications_recipient_type_domain(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_notifications(backend_conn, recipient_type="device")

    for recipient_type in ("staff", "parent"):
        row = make_notifications(backend_conn, recipient_type=recipient_type)
        assert row["recipient_type"] == recipient_type


def test_notifications_event_domain(backend_conn: Conn) -> None:
    for event in ("pickup.unknown", "HOMEWORK.DONE", "nfc.punched"):
        with pg_error(backend_conn, CHECK_VIOLATION) as err:
            make_notifications(backend_conn, event=event)
        assert err.constraint_name == "ck_notifications_event", event

    for event in ("homework.done", "pickup.cancelled"):
        assert make_notifications(backend_conn, event=event)["event"] == event


def test_notifications_event_domain_matches_backend_events(backend_conn: Conn) -> None:
    assert len(Event) == 13
    for event in Event:
        assert make_notifications(backend_conn, event=event.value)["event"] == event.value


def test_notifications_payload_must_be_object(backend_conn: Conn) -> None:
    payloads: tuple[object, ...] = ([], "text", 1)
    for payload in payloads:
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_notifications(backend_conn, payload=Jsonb(payload))

    assert make_notifications(backend_conn)["payload"] == {}
    row = make_notifications(backend_conn, payload=Jsonb({"student_id": "s-1"}))
    assert row["payload"] == {"student_id": "s-1"}


def test_notifications_title_body_length(backend_conn: Conn) -> None:
    for overrides in ({"title": ""}, {"title": "通" * 101}, {"body": "通" * 1001}):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_notifications(backend_conn, **overrides)

    row = make_notifications(backend_conn, title="通" * 100, body="通" * 1000)
    assert (len(row["title"]), len(row["body"])) == (100, 1000)
    assert make_notifications(backend_conn, body="")["body"] == ""


def test_notifications_unread_index_exists(backend_conn: Conn) -> None:
    rows = backend_conn.execute(
        "select indexname, indexdef from pg_indexes "
        "where schemaname = 'public' and tablename = 'notifications'"
    ).fetchall()
    indexes: dict[str, str] = {name: indexdef for name, indexdef in rows}

    unread = indexes["ix_notifications_recipient_unread"]
    assert "(recipient_type, recipient_id)" in unread
    assert "WHERE (read_at IS NULL)" in unread
    created = indexes["ix_notifications_recipient_created"]
    assert "(recipient_type, recipient_id, created_at DESC)" in created


def test_notifications_defaults(backend_conn: Conn) -> None:
    row = make_notifications(backend_conn)

    assert (row["payload"], row["read_at"]) == ({}, None)


def test_notifications_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.notifications")

    row = make_notifications(backend_conn)
    assert_backend_read_write(backend_conn, "public.notifications", row, read_at=READ_AT)


def test_notifications_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_notifications(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.notifications", row["id"], read_at=READ_AT)
