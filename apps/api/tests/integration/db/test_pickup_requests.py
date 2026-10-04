"""DB-025：pickup_requests 表（同生同日一筆非終態、值域、各欄位一致性、FK restrict）。

每條 SQLSTATE 反例只違反一個約束，並以 constraint 名稱斷言是哪一條擋下。
"""

from datetime import UTC, date, datetime, time
from typing import Any
from uuid import uuid4

from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    FK_VIOLATION,
    SEEDED_UPDATED_AT,
    UNIQUE_VIOLATION,
    Conn,
    assert_backend_grants,
    assert_backend_read_write,
    assert_updated_at_trigger,
    pg_error,
)
from tests.integration.db.factories import (
    make_guardians,
    make_pickup_authorizations,
    make_pickup_requests,
    make_staff_users,
)

NOW = datetime(2026, 10, 5, 17, 0, tzinfo=UTC)


def _expect_check(conn: Conn, constraint: str, **overrides: Any) -> None:
    with pg_error(conn, CHECK_VIOLATION) as err:
        make_pickup_requests(conn, **overrides)
    assert err.constraint_name == constraint


def test_pickup_requests_one_open_per_student_day(backend_conn: Conn) -> None:
    first = make_pickup_requests(backend_conn, status="pending")

    with pg_error(backend_conn, UNIQUE_VIOLATION) as err:
        make_pickup_requests(backend_conn, student_id=first["student_id"], status="acknowledged")
    assert err.constraint_name == "uq_pickup_requests_one_open"

    # 不同學生、或同一學生不同日不受影響
    make_pickup_requests(backend_conn)
    make_pickup_requests(
        backend_conn, student_id=first["student_id"], service_date=date(2026, 10, 6)
    )


def test_pickup_requests_new_after_terminal(backend_conn: Conn) -> None:
    first = make_pickup_requests(backend_conn)
    backend_conn.execute(
        "update public.pickup_requests set status = 'cancelled', cancelled_at = %s where id = %s",
        (NOW, first["id"]),
    )
    second = make_pickup_requests(backend_conn, student_id=first["student_id"])
    assert second["status"] == "pending"

    backend_conn.execute(
        "update public.pickup_requests set status = 'expired' where id = %s", (second["id"],)
    )
    third = make_pickup_requests(backend_conn, student_id=first["student_id"])
    assert third["status"] == "pending"


def test_pickup_requests_enum_domains(backend_conn: Conn) -> None:
    _expect_check(backend_conn, "pickup_requests_status_check", status="done")
    _expect_check(backend_conn, "pickup_requests_source_check", source="bus")
    _expect_check(
        backend_conn, "pickup_requests_requested_by_type_check", requested_by_type="system"
    )
    _expect_check(
        backend_conn,
        "pickup_requests_homework_status_at_request_check",
        homework_status_at_request="finished",
    )
    # 其餘欄位維持一致，只讓 reply_source / completion_method 違反值域
    _expect_check(
        backend_conn, "pickup_requests_reply_source_check", reply_source="system", replied_at=NOW
    )
    _expect_check(
        backend_conn,
        "pickup_requests_completion_method_check",
        status="completed",
        completed_at=NOW,
        completion_method="face",
    )


def test_pickup_requests_text_lengths(backend_conn: Conn) -> None:
    _expect_check(backend_conn, "pickup_requests_reply_message_check", reply_message="訊" * 201)
    _expect_check(backend_conn, "pickup_requests_cancel_reason_check", cancel_reason="因" * 201)

    row = make_pickup_requests(backend_conn, reply_message="訊" * 200, reply_ready_eta=time(18, 0))
    assert row["reply_ready_eta"] == time(18, 0)


def test_pickup_requests_reply_consistency(backend_conn: Conn) -> None:
    staff = make_staff_users(backend_conn)
    _expect_check(
        backend_conn,
        "ck_pickup_requests_reply",
        reply_source="staff",
        replied_at=NOW,
        replied_by=None,
    )
    _expect_check(
        backend_conn,
        "ck_pickup_requests_reply",
        reply_source="auto",
        replied_at=NOW,
        replied_by=staff["id"],
    )
    _expect_check(backend_conn, "ck_pickup_requests_reply", reply_source=None, replied_at=NOW)

    auto = make_pickup_requests(backend_conn, reply_source="auto", replied_at=NOW)
    staff_reply = make_pickup_requests(
        backend_conn, reply_source="staff", replied_at=NOW, replied_by=staff["id"]
    )
    assert (auto["replied_by"], staff_reply["replied_by"]) == (None, staff["id"])


def test_pickup_requests_completed_consistency(backend_conn: Conn) -> None:
    guardian = make_guardians(backend_conn)
    _expect_check(
        backend_conn, "ck_pickup_requests_completed", status="completed", completed_at=None
    )
    _expect_check(
        backend_conn,
        "ck_pickup_requests_completed",
        status="pending",
        completed_at=NOW,
        completion_method="override",
    )
    # completed_at 與 completion_method 必須同時出現
    _expect_check(
        backend_conn, "ck_pickup_requests_completed", status="completed", completed_at=NOW
    )
    _expect_check(
        backend_conn,
        "ck_pickup_requests_completion_target",
        status="completed",
        completed_at=NOW,
        completion_method="guardian",
        picked_up_by_guardian_id=None,
    )

    row = make_pickup_requests(
        backend_conn,
        student_id=guardian["student_id"],
        status="completed",
        completed_at=NOW,
        completion_method="guardian",
        picked_up_by_guardian_id=guardian["id"],
    )
    assert row["completion_method"] == "guardian"


def test_pickup_requests_completion_target_exclusive(backend_conn: Conn) -> None:
    guardian = make_guardians(backend_conn)
    authorization = make_pickup_authorizations(backend_conn)
    completed: dict[str, Any] = {"status": "completed", "completed_at": NOW}

    _expect_check(
        backend_conn,
        "ck_pickup_requests_completion_target",
        **completed,
        completion_method="override",
        picked_up_by_guardian_id=guardian["id"],
        picked_up_by_authorization_id=authorization["id"],
    )
    _expect_check(
        backend_conn,
        "ck_pickup_requests_completion_target",
        **completed,
        completion_method="code",
        picked_up_by_authorization_id=None,
    )
    _expect_check(
        backend_conn,
        "ck_pickup_requests_completion_target",
        **completed,
        completion_method="visual_match",
        picked_up_by_authorization_id=None,
    )

    row = make_pickup_requests(
        backend_conn,
        **completed,
        completion_method="code",
        picked_up_by_authorization_id=authorization["id"],
    )
    assert row["picked_up_by_authorization_id"] == authorization["id"]
    # override 可以不指定接走對象
    assert (
        make_pickup_requests(backend_conn, **completed, completion_method="override")[
            "completion_method"
        ]
        == "override"
    )


def test_pickup_requests_cancel_and_arrive_consistency(backend_conn: Conn) -> None:
    _expect_check(backend_conn, "ck_pickup_requests_cancelled", status="cancelled")
    _expect_check(backend_conn, "ck_pickup_requests_cancelled", status="pending", cancelled_at=NOW)
    _expect_check(backend_conn, "ck_pickup_requests_arrived", status="arrived")

    arrived = make_pickup_requests(backend_conn, status="arrived", arrived_at=NOW)
    assert arrived["arrived_at"] == NOW


def test_pickup_requests_fk_restrict(backend_conn: Conn) -> None:
    guardian = make_guardians(backend_conn)
    make_pickup_requests(
        backend_conn,
        student_id=guardian["student_id"],
        status="completed",
        completed_at=NOW,
        completion_method="guardian",
        picked_up_by_guardian_id=guardian["id"],
    )
    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute("delete from public.guardians where id = %s", (guardian["id"],))

    authorization = make_pickup_authorizations(backend_conn)
    make_pickup_requests(
        backend_conn,
        student_id=authorization["student_id"],
        status="completed",
        completed_at=NOW,
        completion_method="code",
        picked_up_by_authorization_id=authorization["id"],
    )
    with pg_error(backend_conn, FK_VIOLATION):
        backend_conn.execute(
            "delete from public.pickup_authorizations where id = %s", (authorization["id"],)
        )

    with pg_error(backend_conn, FK_VIOLATION):
        make_pickup_requests(backend_conn, student_id=uuid4())

    with pg_error(backend_conn, FK_VIOLATION):
        make_pickup_requests(backend_conn, reply_source="staff", replied_at=NOW, replied_by=uuid4())


def test_pickup_requests_staff_set_null(backend_conn: Conn) -> None:
    staff = make_staff_users(backend_conn)
    done = make_pickup_requests(
        backend_conn,
        status="completed",
        completed_at=NOW,
        completion_method="override",
        completed_by=staff["id"],
    )

    backend_conn.execute("delete from public.staff_users where id = %s", (staff["id"],))

    after = backend_conn.execute(
        "select completed_by from public.pickup_requests where id = %s", (done["id"],)
    ).fetchone()
    assert after == (None,)


def test_pickup_requests_grants(owner_conn: Conn, backend_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.pickup_requests")

    row = make_pickup_requests(backend_conn)
    assert_backend_read_write(backend_conn, "public.pickup_requests", row, cancel_reason="改期")


def test_pickup_requests_updated_at_trigger(backend_conn: Conn) -> None:
    row = make_pickup_requests(backend_conn, updated_at=SEEDED_UPDATED_AT)

    assert_updated_at_trigger(
        backend_conn, "public.pickup_requests", row["id"], cancel_reason="改期"
    )
