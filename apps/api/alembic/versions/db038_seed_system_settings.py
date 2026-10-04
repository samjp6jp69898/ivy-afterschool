"""seed_system_settings：system_settings 首批 key 的預設值（data migration，M2；DB-038）。

系統必要的預設資料在每個環境都必須存在，與 schema 一起由 alembic upgrade head 建立。冪等
（on conflict (key) do nothing），不覆寫後台改過的值。value 的 JSON 形狀是與 BACKEND
app/core/settings_registry.py 的介面，registry 的 schema 與預設值必須和這裡一致。時間字串一律
HH:MM（Asia/Taipei 當地時間）。org.service_hours 與 pickup.window 為可運作的佔位預設值，上線前
由管理者於後台確認調整。測試以同一個 SEED_SQL 常數重跑驗證冪等。

Revision ID: db038
Revises: db031
Create Date: 2026-10-04 20:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db038"
down_revision: str | Sequence[str] | None = "db031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SEED_SQL = """
insert into public.system_settings (key, value, is_secret) values
    ('org.profile',
     '{"name": "", "address": "", "phone": "", "logo_url": null}'::jsonb, false),
    ('org.service_hours', '{
        "mon": {"open": true, "start": "12:00", "end": "19:00"},
        "tue": {"open": true, "start": "12:00", "end": "19:00"},
        "wed": {"open": true, "start": "12:00", "end": "19:00"},
        "thu": {"open": true, "start": "12:00", "end": "19:00"},
        "fri": {"open": true, "start": "12:00", "end": "19:00"},
        "sat": {"open": false, "start": "08:00", "end": "12:00"}
    }'::jsonb, false),
    ('pickup.window', '{
        "request_start": "12:00",
        "request_end": "19:00",
        "latest_expected_arrival": "19:00",
        "auto_expire_minutes": 120
    }'::jsonb, false),
    ('homework.defaults', '{
        "auto_reply_without_eta": true,
        "no_eta_reply_text": "已通知老師，稍後回覆預計時間"
    }'::jsonb, false),
    ('notification.toggles', '{
        "attendance.checked_in": true,
        "attendance.checked_out": true,
        "leave.created": true,
        "leave.cancelled": true,
        "homework.eta_updated": true,
        "homework.done": true,
        "pickup.requested": true,
        "pickup.replied": true,
        "pickup.arrived": true,
        "pickup.completed": true,
        "pickup.cancelled": true,
        "exam.published": true,
        "binding.completed": true
    }'::jsonb, false),
    ('line.liff',
     '{"liff_id": "", "channel_id": "", "add_friend_url": ""}'::jsonb, false),
    ('leave.window', '{
        "past_days": 30,
        "future_days": 60,
        "max_attachments": 3,
        "max_attachment_mb": 10
    }'::jsonb, false),
    ('pickup.authorization',
     '{"max_days_ahead": 14, "max_active_per_day": 3}'::jsonb, false),
    ('pickup.persons', '{"max_per_student": 10}'::jsonb, false),
    ('homework.window', '{"past_days": 30, "future_days": 7}'::jsonb, false),
    -- 未設定前為 null；BACKEND 寫入時以應用層加密後存密文字串，DB 永不存明文
    ('line.messaging',
     '{"channel_access_token": null, "channel_secret": null}'::jsonb, true)
on conflict (key) do nothing
"""


def upgrade() -> None:
    op.execute(SEED_SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
