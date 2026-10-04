"""DB 整合測試的測資 factory（DB-002）。

各表 task 在此補自己的 `make_<table>(conn, **overrides)`，預設值用擬真假資料
（學生「王小明」、電話 `0912-000-001`），寫入一律經 `backend_conn`。
"""

import secrets
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def split_table(table: str) -> tuple[str, str]:
    """`schema.name` 拆成 (schema, name)；未帶 schema 時為 `public`。"""
    schema, _, name = table.rpartition(".")
    return schema or "public", name


def insert_row(conn: psycopg.Connection[Any], table: str, **cols: Any) -> dict[str, Any]:
    """`insert into <table> (...) values (...) returning *`，回傳插入後的整列。

    `table` 可寫 `schema.name`，未帶 schema 時為 `public`；沒有欄位時以 default values 插入。
    """
    target = sql.Identifier(*split_table(table))
    if cols:
        query = sql.SQL("insert into {} ({}) values ({}) returning *").format(
            target,
            sql.SQL(", ").join(sql.Identifier(col) for col in cols),
            sql.SQL(", ").join(sql.Placeholder() for _ in cols),
        )
    else:
        query = sql.SQL("insert into {} default values returning *").format(target)
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(query, list(cols.values())).fetchone()
    assert row is not None, f"insert into {table} 沒有回傳列"
    return row


# 各表 factory：預設值避開 data migration seed 的值（DB-035~037），可直接在已 seed 的 DB 上插入


def make_roles(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    return insert_row(
        conn, "public.roles", **{"code": "counselor", "name": "輔導老師", **overrides}
    )


def make_subjects(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    return insert_row(conn, "public.subjects", **{"name": "書法", **overrides})


def make_exam_types(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    return insert_row(conn, "public.exam_types", **{"name": "週考", **overrides})


def make_schools(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    return insert_row(conn, "public.schools", **{"name": "臺北市大安區新生國小", **overrides})


def make_closed_days(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    return insert_row(
        conn, "public.closed_days", **{"date": date(2026, 10, 10), "reason": "國慶日", **overrides}
    )


def make_classes(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    defaults = {"name": "低年級 A 班", "grade_levels": [1, 2], "academic_year": 115}
    return insert_row(conn, "public.classes", **{**defaults, **overrides})


# 合法格式的 argon2id 編碼字串（假值，不對應任何密碼）
ARGON2ID_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQxMjM0$aGFzaGhhc2hoYXNoaGFzaGhhc2g"


def make_staff_users(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """未指定 role_id 時另建一個專用角色；username 預設帶亂數，同一測試可多次呼叫。"""
    if "role_id" not in overrides:
        overrides["role_id"] = make_roles(conn, code=f"role_{uuid4().hex[:12]}")["id"]
    defaults = {
        "username": f"teacher.{uuid4().hex[:8]}",
        "password_hash": ARGON2ID_HASH,
        "display_name": "林老師",
    }
    return insert_row(conn, "public.staff_users", **{**defaults, **overrides})


def make_refresh_tokens(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """token_hash 預設為亂數 sha256 hex，同一測試可多次呼叫；expires_at 預設為遠未來的固定時間。"""
    defaults = {
        "subject_type": "staff",
        "subject_id": uuid4(),
        "family_id": uuid4(),
        "token_hash": secrets.token_hex(32),
        "expires_at": datetime(2099, 1, 1, tzinfo=UTC),
    }
    return insert_row(conn, "public.refresh_tokens", **{**defaults, **overrides})


def make_audit_logs(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    defaults = {
        "actor_type": "staff",
        "actor_id": uuid4(),
        "action": "settings.update",
        "entity_type": "system_settings",
        "entity_id": "org.profile",
    }
    return insert_row(conn, "public.audit_logs", **{**defaults, **overrides})


def make_students(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """student_no 預設帶亂數，同一測試可多次呼叫。"""
    defaults = {"student_no": f"S{uuid4().hex[:10]}", "name": "王小明", "grade_level": 3}
    return insert_row(conn, "public.students", **{**defaults, **overrides})


def make_parent_accounts(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """line_user_id 預設為亂數的合法 LINE userId，同一測試可多次呼叫。"""
    defaults = {"line_user_id": f"U{uuid4().hex}", "display_name": "王媽媽"}
    return insert_row(conn, "public.parent_accounts", **{**defaults, **overrides})


def make_notifications(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    defaults = {
        "recipient_type": "parent",
        "recipient_id": uuid4(),
        "event": "homework.done",
        "title": "作業已完成",
        "body": "王小明的作業已完成，可以來接送了",
    }
    return insert_row(conn, "public.notifications", **{**defaults, **overrides})


def make_system_settings(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """key 預設帶亂數後綴字母（key 格式為 `<段>.<段>`），同一測試可多次呼叫。"""
    suffix = "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(10))
    defaults = {"key": f"test.setting_{suffix}", "value": Jsonb({"enabled": True})}
    return insert_row(conn, "public.system_settings", **{**defaults, **overrides})


def make_class_staff(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """未指定 class_id / staff_user_id 時各另建一筆（班名帶亂數，同一測試可多次呼叫）。"""
    defaults = {
        "class_id": overrides.get("class_id")
        or make_classes(conn, name=f"班{uuid4().hex[:8]}")["id"],
        "staff_user_id": overrides.get("staff_user_id") or make_staff_users(conn)["id"],
    }
    return insert_row(conn, "public.class_staff", **{**defaults, **overrides})


def make_guardians(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """未指定 student_id 時另建一位學生。"""
    defaults = {
        "student_id": overrides.get("student_id") or make_students(conn)["id"],
        "name": "王大明",
        "relation": "father",
    }
    return insert_row(conn, "public.guardians", **{**defaults, **overrides})


def make_student_leaves(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """未指定 student_id 時另建一位學生；預設為單日 active 請假（2026-10-05）。"""
    defaults = {
        "student_id": overrides.get("student_id") or make_students(conn)["id"],
        "leave_type": "sick",
        "start_date": date(2026, 10, 5),
        "end_date": date(2026, 10, 5),
        "created_by_type": "parent",
        "created_by_id": uuid4(),
    }
    return insert_row(conn, "public.student_leaves", **{**defaults, **overrides})


def make_homework_items(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """未指定 student_id 時另建一位學生。"""
    defaults = {
        "student_id": overrides.get("student_id") or make_students(conn)["id"],
        "service_date": date(2026, 10, 5),
        "title": "數學習作 p.12-13",
    }
    return insert_row(conn, "public.homework_items", **{**defaults, **overrides})


def make_homework_daily_progress(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """未指定 student_id 時另建一位學生。"""
    defaults = {
        "student_id": overrides.get("student_id") or make_students(conn)["id"],
        "service_date": date(2026, 10, 5),
    }
    return insert_row(conn, "public.homework_daily_progress", **{**defaults, **overrides})


def make_pickup_persons(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """未指定 student_id 時另建一位學生。"""
    defaults = {
        "student_id": overrides.get("student_id") or make_students(conn)["id"],
        "name": "李阿姨",
        "relation": "阿姨",
        "phone": "0912-000-001",
    }
    return insert_row(conn, "public.pickup_persons", **{**defaults, **overrides})


def make_exams(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """未指定 exam_type_id 時另建一個考試類型；預設以 grade_level=3 為應考範圍。"""
    defaults = {
        "name": "第一次段考",
        "exam_type_id": overrides.get("exam_type_id")
        or make_exam_types(conn, name=f"類型{uuid4().hex[:8]}")["id"],
        "exam_date": date(2026, 10, 20),
    }
    if "class_id" not in overrides:
        defaults["grade_level"] = 3
    return insert_row(conn, "public.exams", **{**defaults, **overrides})


def make_parent_binding_codes(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """未指定 guardian_id / created_by 時各另建一筆；code_hash 預設為亂數 64 碼 hex。"""
    defaults = {
        "guardian_id": overrides.get("guardian_id") or make_guardians(conn)["id"],
        "code_hash": secrets.token_hex(32),
        "expires_at": datetime(2099, 1, 1, tzinfo=UTC),
        "created_by": overrides.get("created_by") or make_staff_users(conn)["id"],
    }
    return insert_row(conn, "public.parent_binding_codes", **{**defaults, **overrides})


def make_student_leave_attachments(
    conn: psycopg.Connection[Any], **overrides: Any
) -> dict[str, Any]:
    """未指定 leave_id 時另建一筆請假；storage_path 為 `<leave_id>/<uuid4 hex>.pdf`。"""
    leave_id = overrides.get("leave_id") or make_student_leaves(conn)["id"]
    defaults = {
        "leave_id": leave_id,
        "storage_path": f"{leave_id}/{uuid4().hex}.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 204800,
    }
    return insert_row(conn, "public.student_leave_attachments", **{**defaults, **overrides})


def make_student_attendances(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """未指定 student_id 時另建一位學生；預設為 2026-10-05 的 expected。"""
    defaults = {
        "student_id": overrides.get("student_id") or make_students(conn)["id"],
        "service_date": date(2026, 10, 5),
    }
    return insert_row(conn, "public.student_attendances", **{**defaults, **overrides})


def make_pickup_authorizations(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """未指定 student_id 時另建一位學生；code_hash 預設為亂數 64 碼 hex。"""
    defaults = {
        "student_id": overrides.get("student_id") or make_students(conn)["id"],
        "service_date": date(2026, 10, 5),
        "proxy_name": "李阿姨",
        "proxy_phone": "0912-000-001",
        "code_hash": secrets.token_hex(32),
        "code_last4": "1234",
    }
    return insert_row(conn, "public.pickup_authorizations", **{**defaults, **overrides})


def make_exam_subjects(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """未指定 exam_id / subject_id 時各另建一筆（科目名帶亂數，同一測試可多次呼叫）。"""
    defaults = {
        "exam_id": overrides.get("exam_id") or make_exams(conn)["id"],
        "subject_id": overrides.get("subject_id")
        or make_subjects(conn, name=f"科目{uuid4().hex[:8]}")["id"],
    }
    return insert_row(conn, "public.exam_subjects", **{**defaults, **overrides})


def make_notification_outbox(conn: psycopg.Connection[Any], **overrides: Any) -> dict[str, Any]:
    """未指定 notification_id 時另建一筆 notification。"""
    defaults = {
        "notification_id": overrides.get("notification_id") or make_notifications(conn)["id"]
    }
    return insert_row(conn, "public.notification_outbox", **{**defaults, **overrides})


def make_notification_preferences(
    conn: psycopg.Connection[Any], **overrides: Any
) -> dict[str, Any]:
    """未指定 parent_account_id 時另建一個家長帳號。"""
    defaults = {
        "parent_account_id": overrides.get("parent_account_id") or make_parent_accounts(conn)["id"],
        "event": "homework.done",
    }
    return insert_row(conn, "public.notification_preferences", **{**defaults, **overrides})
