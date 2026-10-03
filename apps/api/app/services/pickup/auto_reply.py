"""BACKEND-403：家長按「我要來接」後系統立即自動回覆（domain_spec M7 流程 2），純函式。

1. 作業整體 done → 「作業已完成，可以接送」。
2. 未完成且有 ready_eta → 「預計 HH:MM 可接送」，note 有值時換行接 note。
3. 未完成且無 ETA → 依 ``homework.defaults`` 決定是否回覆設定的文案；兩種情況都需要員工回覆。

progress 為 None 視為 not_started、無 ETA。reply_message 上限 200 字（DB-025 CHECK）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Final, Literal

from app.core.settings_registry import HomeworkDefaults
from app.services.homework_rules import OverallStatus

DONE_REPLY_TEXT: Final = "作業已完成，可以接送"
REPLY_MESSAGE_MAX: Final = 200


@dataclass(frozen=True)
class ProgressSnapshot:
    overall_status: OverallStatus
    ready_eta: time | None
    note: str | None


@dataclass(frozen=True)
class AutoReply:
    homework_status: OverallStatus
    reply_ready_eta: time | None
    reply_message: str | None
    reply_source: Literal["auto"] | None
    needs_staff_reply: bool


def compute_auto_reply(progress: ProgressSnapshot | None, defaults: HomeworkDefaults) -> AutoReply:
    status: OverallStatus = progress.overall_status if progress is not None else "not_started"
    eta = progress.ready_eta if progress is not None else None

    if status == "done":
        return AutoReply(status, None, DONE_REPLY_TEXT, "auto", needs_staff_reply=False)

    if eta is not None:
        message = f"預計 {eta:%H:%M} 可接送"
        note = progress.note if progress is not None else None
        if note is not None and note.strip():
            message += f"\n{note}"
        return AutoReply(status, eta, message[:REPLY_MESSAGE_MAX], "auto", needs_staff_reply=False)

    if defaults.auto_reply_without_eta:
        return AutoReply(
            status,
            None,
            defaults.no_eta_reply_text[:REPLY_MESSAGE_MAX],
            "auto",
            needs_staff_reply=True,
        )
    return AutoReply(status, None, None, None, needs_staff_reply=True)
