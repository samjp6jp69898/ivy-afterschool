"""create_exam_scores：exam_scores（考試成績，含分數上限 trigger，domain_spec M8；DB-028）。

新功能，ivy 無對應。後台以「學生 x 科目」格狀表格逐格自動儲存，BACKEND 以
`insert ... on conflict (exam_id, student_id, subject_id) do update` 批次 upsert。

`score <= full_score` 跨表，CHECK 做不到，以兩支 trigger 守住（DB-027 說明的下修保護也在這裡建立）：
- enforce_exam_score_within_full_score：登分 / 改分 / 改科目時，分數不得超過該科 full_score。
- guard_exam_subject_full_score：下修 full_score 時，不得低於該科既有分數。
兩者都以 check_violation（23514）回報並帶 constraint 名稱。分數 trigger 讀 exam_subjects 時
`for share`，與下修 trigger 取得的列鎖互斥，兩邊併發時不會讓「分數 > 滿分」的資料同時成立。
trigger function 以 security invoker 執行（app_backend 對兩張表皆有 select）；trigger 觸發時不檢查
呼叫者的 EXECUTE 權限，因此不需 grant execute，建立後 revoke all from public。

Revision ID: db028
Revises: db025
Create Date: 2026-10-04 22:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "db028"
down_revision: str | Sequence[str] | None = "db025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATEMENTS = (
    """
    create table public.exam_scores (
        id uuid primary key default gen_random_uuid(),
        exam_id uuid not null references public.exams (id) on delete cascade,
        student_id uuid not null references public.students (id) on delete restrict,
        subject_id uuid not null,
        score numeric(6, 2) null,
        is_absent boolean not null default false,
        note text null,
        updated_by uuid null references public.staff_users (id) on delete set null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        constraint uq_exam_scores_exam_student_subject unique (exam_id, student_id, subject_id),
        -- 只能為該考試有設定的科目登分；從考試移除科目時連帶刪除該科成績
        constraint fk_exam_scores_exam_subject foreign key (exam_id, subject_id)
            references public.exam_subjects (exam_id, subject_id) on delete cascade,
        check (score is null or score >= 0),
        -- 缺考不可同時有分數
        constraint ck_exam_scores_absent_no_score check (not (is_absent and score is not null)),
        check (note is null or length(note) <= 200)
    )
    """,
    # 個人歷次成績、家長端
    "create index ix_exam_scores_student on public.exam_scores (student_id, exam_id)",
    # 各科平均
    "create index ix_exam_scores_exam_subject on public.exam_scores (exam_id, subject_id)",
    """
    create index ix_exam_scores_updated_by on public.exam_scores (updated_by)
    where updated_by is not null
    """,
    """
    create function public.enforce_exam_score_within_full_score() returns trigger
    language plpgsql
    as $$
    declare
        v_full numeric(6, 2);
    begin
        if new.score is null then
            return new;
        end if;
        select full_score into v_full
        from public.exam_subjects
        where exam_id = new.exam_id and subject_id = new.subject_id
        for share;
        -- 找不到科目時交給複合 FK 回報 23503
        if v_full is not null and new.score > v_full then
            raise exception using
                errcode = 'check_violation',
                message = format('score %s exceeds full_score %s', new.score, v_full),
                constraint = 'ck_exam_scores_within_full_score';
        end if;
        return new;
    end
    $$
    """,
    """
    create trigger trg_exam_scores_full_score
    before insert or update of score, subject_id, exam_id on public.exam_scores
    for each row execute function public.enforce_exam_score_within_full_score()
    """,
    """
    create function public.guard_exam_subject_full_score() returns trigger
    language plpgsql
    as $$
    begin
        if exists (
            select 1 from public.exam_scores
            where exam_id = new.exam_id and subject_id = new.subject_id
              and score > new.full_score
        ) then
            raise exception using
                errcode = 'check_violation',
                message = 'full_score lower than existing scores',
                constraint = 'ck_exam_subjects_full_score_floor';
        end if;
        return new;
    end
    $$
    """,
    """
    create trigger trg_exam_subjects_full_score_guard
    before update of full_score on public.exam_subjects
    for each row execute function public.guard_exam_subject_full_score()
    """,
    "revoke all on function public.enforce_exam_score_within_full_score() from public",
    "revoke all on function public.guard_exam_subject_full_score() from public",
    """
    create trigger trg_exam_scores_updated_at before update on public.exam_scores
    for each row execute function public.set_updated_at()
    """,
    "call app_private.grant_backend('public.exam_scores')",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("forward-only：以新的 revision 修正，不回滾")
