"""BACKEND-372：作業進度的純規則函式（domain_spec M6）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal, get_args

ItemStatus = Literal["todo", "doing", "correcting", "done"]
OverallStatus = Literal["not_started", "in_progress", "done"]

ITEM_STATUSES: Final[frozenset[str]] = frozenset(get_args(ItemStatus))


def derive_overall_status(item_statuses: Sequence[str]) -> OverallStatus:
    """無 item 或全部 todo → not_started；全部 done → done；其餘 → in_progress。"""
    unknown = [s for s in item_statuses if s not in ITEM_STATUSES]
    if unknown:
        raise ValueError(f"未知的作業項目狀態：{unknown}")
    if not item_statuses or all(s == "todo" for s in item_statuses):
        return "not_started"
    if all(s == "done" for s in item_statuses):
        return "done"
    return "in_progress"
