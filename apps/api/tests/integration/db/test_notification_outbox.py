"""DB-030：notification_outbox 表（每通知每頻道唯一、值域、attempts、錯誤必填、cascade）。"""

from datetime import UTC, datetime

from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    SEEDED_UPDATED_AT,
    UNIQUE_VIOLATION,
    Conn,
    assert_backend_grants,
    assert_backend_read_write,
    assert_updated_at_trigger,
    connect_backend,
    connect_owner,
    pg_error,
)
from tests.integration.db.factories import make_notification_outbox, make_notifications

PAST = datetime(2026, 1, 1, tzinfo=UTC)

PICKUP_SQL = """
    select id from public.notification_outbox
    where status = 'pending' and next_attempt_at <= now() and notification_id = any(%s)
    order by next_attempt_at, id
    limit 2
    for update skip locked
"""


def test_notification_outbox_unique_channel(backend_conn: Conn) -> None:
    first = make_notification_outbox(backend_conn)

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_notification_outbox(backend_conn, notification_id=first["notification_id"])
    assert err.constraint_name == "uq_notification_outbox_notification_channel"


def test_notification_outbox_defaults(backend_conn: Conn) -> None:
    row = make_notification_outbox(backend_conn)

    assert (row["channel"], row["status"], row["attempts"]) == ("line", "pending", 0)
    assert row["last_error"] is None


def test_notification_outbox_enum_domains(backend_conn: Conn) -> None:
    for bad in ({"channel": "in_app"}, {"channel": "ws"}, {"status": "retrying"}):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_notification_outbox(backend_conn, **bad)


def test_notification_outbox_attempts_range(backend_conn: Conn) -> None:
    for bad_attempts in (-1, 21):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_notification_outbox(backend_conn, attempts=bad_attempts)

    assert make_notification_outbox(backend_conn, attempts=20)["attempts"] == 20


def test_notification_outbox_failed_requires_error(backend_conn: Conn) -> None:
    for status in ("dead", "failed"):
        with pg_error(backend_conn, CHECK_VIOLATION) as err:
            make_notification_outbox(backend_conn, status=status, last_error=None)
        assert err.constraint_name == "ck_notification_outbox_dead_has_error"

    row = make_notification_outbox(backend_conn, status="dead", last_error="LINE 429")
    assert row["last_error"] == "LINE 429"


def test_notification_outbox_last_error_length(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_notification_outbox(backend_conn, status="failed", last_error="e" * 2001)


def test_notification_outbox_cascade(backend_conn: Conn) -> None:
    row = make_notification_outbox(backend_conn)

    backend_conn.execute(
        "delete from public.notifications where id = %s", (row["notification_id"],)
    )

    remaining = backend_conn.execute(
        "select count(*) from public.notification_outbox where id = %s", (row["id"],)
    ).fetchone()
    assert remaining == (0,)


def test_notification_outbox_due_index_is_partial(owner_conn: Conn) -> None:
    row = owner_conn.execute(
        """
        select i.indpred is not null, pg_get_expr(i.indpred, i.indrelid)
        from pg_index i
        where i.indexrelid = 'public.ix_notification_outbox_due'::regclass
        """
    ).fetchone()

    assert row is not None
    assert row[0] is True
    assert "pending" in row[1]
    assert "failed" in row[1]


def test_notification_outbox_skip_locked_pickup() -> None:
    """資料必須 commit 才看得到：兩條 app_backend 連線，結束後以 owner 清除測資。"""
    conn_a, conn_b, conn_setup = connect_backend(), connect_backend(), connect_backend()
    notification_ids: list[object] = []
    try:
        with conn_setup.transaction():
            for _ in range(3):
                notification = make_notifications(conn_setup)
                make_notification_outbox(
                    conn_setup, notification_id=notification["id"], next_attempt_at=PAST
                )
                notification_ids.append(notification["id"])

        rows_a = conn_a.execute(PICKUP_SQL, (notification_ids,)).fetchall()
        rows_b = conn_b.execute(PICKUP_SQL, (notification_ids,)).fetchall()

        assert len(rows_a) == 2
        assert len(rows_b) == 1
        assert not {r[0] for r in rows_a} & {r[0] for r in rows_b}
    finally:
        conn_a.rollback()
        conn_b.rollback()
        for conn in (conn_a, conn_b, conn_setup):
            conn.close()
        if notification_ids:
            with connect_owner() as owner:
                owner.execute(
                    "delete from public.notifications where id = any(%s)", (notification_ids,)
                )
                owner.commit()


def test_notification_outbox_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.notification_outbox")

    row = make_notification_outbox(backend_conn)
    assert_backend_read_write(
        backend_conn, "public.notification_outbox", row, status="sent", attempts=1
    )


def test_notification_outbox_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_notification_outbox(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(backend_conn, "public.notification_outbox", row["id"], attempts=1)
