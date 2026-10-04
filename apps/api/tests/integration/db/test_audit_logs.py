"""DB-006：audit_logs 表（actor_type、actor_id、action 格式、append-only grant、trigger）。"""

from psycopg.types.json import Jsonb

from tests.integration.db.conftest import (
    CHECK_VIOLATION,
    INSUFFICIENT_PRIVILEGE,
    Conn,
    assert_backend_grants,
    pg_error,
)
from tests.integration.db.factories import make_audit_logs


def test_audit_logs_actor_type_domain(backend_conn: Conn) -> None:
    with pg_error(backend_conn, CHECK_VIOLATION):
        make_audit_logs(backend_conn, actor_type="admin")

    for actor_type in ("staff", "parent", "device"):
        assert make_audit_logs(backend_conn, actor_type=actor_type)["actor_type"] == actor_type


def test_audit_logs_actor_id_required_unless_system(backend_conn: Conn) -> None:
    for actor_type in ("staff", "parent", "device"):
        with pg_error(backend_conn, CHECK_VIOLATION) as err:
            make_audit_logs(backend_conn, actor_type=actor_type, actor_id=None)
        assert err.constraint_name == "ck_audit_logs_actor_id", actor_type

    row = make_audit_logs(backend_conn, actor_type="system", actor_id=None)
    assert (row["actor_type"], row["actor_id"]) == ("system", None)


def test_audit_logs_action_format(backend_conn: Conn) -> None:
    for action in ("UPDATE", "exam_score", "Exam_score.update", "exam-score.update", "a.b.c"):
        with pg_error(backend_conn, CHECK_VIOLATION):
            make_audit_logs(backend_conn, action=action)

    for action in ("exam_score.update", "pickup.override_complete"):
        assert make_audit_logs(backend_conn, action=action)["action"] == action


def test_audit_logs_before_after_ip(backend_conn: Conn) -> None:
    row = make_audit_logs(
        backend_conn,
        before=Jsonb({"score": 80}),
        after=Jsonb({"score": 85}),
        ip="203.0.113.5",
        user_agent="Mozilla/5.0",
    )

    assert (row["before"], row["after"]) == ({"score": 80}, {"score": 85})
    assert str(row["ip"]) == "203.0.113.5"


def test_audit_logs_append_only_for_backend(backend_conn: Conn) -> None:
    row = make_audit_logs(backend_conn)
    assert backend_conn.execute(
        "select action from public.audit_logs where id = %s", (row["id"],)
    ).fetchall() == [("settings.update",)]

    with pg_error(backend_conn, INSUFFICIENT_PRIVILEGE):
        backend_conn.execute(
            "update public.audit_logs set action = 'x.y' where id = %s", (row["id"],)
        )
    with pg_error(backend_conn, INSUFFICIENT_PRIVILEGE):
        backend_conn.execute("delete from public.audit_logs where id = %s", (row["id"],))
    with pg_error(backend_conn, INSUFFICIENT_PRIVILEGE):
        backend_conn.execute("truncate public.audit_logs")

    assert backend_conn.execute(
        "select action from public.audit_logs where id = %s", (row["id"],)
    ).fetchall() == [("settings.update",)]


def test_audit_logs_grants(owner_conn: Conn) -> None:
    assert_backend_grants(owner_conn, "public.audit_logs", frozenset({"SELECT", "INSERT"}))


def test_audit_logs_updated_at_trigger_exists(owner_conn: Conn) -> None:
    rows = owner_conn.execute(
        """
        select t.tgname, p.proname
        from pg_trigger t join pg_proc p on p.oid = t.tgfoid
        where t.tgrelid = 'public.audit_logs'::regclass and not t.tgisinternal
        """
    ).fetchall()
    assert rows == [("trg_audit_logs_updated_at", "set_updated_at")]
