"""create_students：students（學生主檔，domain_spec M3；DB-014）。

參考 ivy Student：保留學號唯一、醫療欄位應用層加密（ivy EncryptedText）、退學日期；去掉 tenant_id、
lifecycle 七態（改為 active / suspended / withdrawn）、政府申報欄位、銷帳碼、緊急聯絡人（改由
guardians）與幼稚園特有欄位。

敏感欄位（id_number_enc、health_note_enc）由後端以 APP_SECRET_KEY 衍生的金鑰做 AES-256-GCM
加解密，DB 只存密文、不持有金鑰（不用 pgcrypto，避免金鑰出現在 SQL 與查詢日誌）。
id_number_hmac 是正規化身分證字號的 HMAC-SHA256 hex（另一個 derivation label），供查重與搜尋；
唯一性含已封存 / 退班學生，同一人再次入班沿用原紀錄。

Revision ID: db014
Revises: db006
Create Date: 2026-10-04 13:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db014"
down_revision: str | Sequence[str] | None = "db006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.students (
        id uuid primary key default gen_random_uuid(),
        student_no text not null,
        name text not null,
        gender text null,
        birthday date null,
        grade_level integer not null,
        school_id uuid null references public.schools (id) on delete restrict,
        school_class text null,
        class_id uuid null references public.classes (id) on delete set null,
        status text not null default 'active',
        enrolled_on date null,
        withdrawn_on date null,
        photo_path text null,
        id_number_enc bytea null,
        id_number_hmac text null,
        health_note_enc bytea null,
        note text null,
        archived_at timestamptz null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        constraint uq_students_student_no unique (student_no),
        check (student_no ~ '^[A-Za-z0-9-]{1,20}$'),
        check (length(btrim(name)) between 1 and 50),
        check (gender in ('male', 'female', 'other')),
        check (grade_level between 1 and 6),
        check (school_class is null or length(school_class) <= 20),
        check (status in ('active', 'suspended', 'withdrawn')),
        constraint ck_students_withdrawn_on
            check (withdrawn_on is null or enrolled_on is null or withdrawn_on >= enrolled_on),
        constraint ck_students_withdrawn_status
            check (status <> 'withdrawn' or withdrawn_on is not null),
        constraint ck_students_id_number_hmac_format
            check (id_number_hmac is null or id_number_hmac ~ '^[0-9a-f]{64}$'),
        constraint ck_students_id_number_pair
            check ((id_number_enc is null) = (id_number_hmac is null))
    )
    """,
    """
    create unique index uq_students_id_number_hmac on public.students (id_number_hmac)
    where id_number_hmac is not null
    """,
    "create index ix_students_class on public.students (class_id) where archived_at is null",
    # 每日出勤初始化撈 active 學生、考試名單依年級
    """
    create index ix_students_status_grade on public.students (status, grade_level)
    where archived_at is null
    """,
    "create index ix_students_school on public.students (school_id)",
    # 後台搜尋
    "create index ix_students_name on public.students (name)",
    """
    create trigger trg_students_updated_at before update on public.students
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.students')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
