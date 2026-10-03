"""BACKEND-403：compute_auto_reply（家長按「我要來接」後的系統自動回覆）。"""

from datetime import time

import pytest

from app.core.settings_registry import HomeworkDefaults
from app.services.pickup.auto_reply import (
    DONE_REPLY_TEXT,
    AutoReply,
    ProgressSnapshot,
    compute_auto_reply,
)

_AUTO_ON = HomeworkDefaults(
    auto_reply_without_eta=True, no_eta_reply_text="已通知老師，稍後回覆預計時間"
)
_AUTO_OFF = HomeworkDefaults(
    auto_reply_without_eta=False, no_eta_reply_text="已通知老師，稍後回覆預計時間"
)


def test_auto_reply_done() -> None:
    result = compute_auto_reply(ProgressSnapshot("done", time(17, 0), None), _AUTO_ON)

    assert result == AutoReply("done", None, "作業已完成，可以接送", "auto", False)
    assert DONE_REPLY_TEXT == "作業已完成，可以接送"


def test_auto_reply_done_ignores_auto_reply_setting() -> None:
    # 完成時一律自動回覆，不看 auto_reply_without_eta
    result = compute_auto_reply(ProgressSnapshot("done", None, "已訂正"), _AUTO_OFF)

    assert result == AutoReply("done", None, DONE_REPLY_TEXT, "auto", False)


def test_auto_reply_with_eta() -> None:
    result = compute_auto_reply(
        ProgressSnapshot("in_progress", time(17, 30), "剩數學訂正"), _AUTO_ON
    )

    assert result == AutoReply(
        "in_progress", time(17, 30), "預計 17:30 可接送\n剩數學訂正", "auto", False
    )


@pytest.mark.parametrize("note", [None, "", "   "])
def test_auto_reply_with_eta_without_note(note: str | None) -> None:
    result = compute_auto_reply(ProgressSnapshot("not_started", time(9, 5), note), _AUTO_OFF)

    # 有 ETA 時即使關閉「無 ETA 自動回覆」也照樣自動回覆；時間補零
    assert result == AutoReply("not_started", time(9, 5), "預計 09:05 可接送", "auto", False)


def test_auto_reply_no_eta_auto_on() -> None:
    result = compute_auto_reply(None, _AUTO_ON)

    assert result == AutoReply("not_started", None, "已通知老師，稍後回覆預計時間", "auto", True)


def test_auto_reply_no_eta_uses_configured_text() -> None:
    defaults = HomeworkDefaults(auto_reply_without_eta=True, no_eta_reply_text="老師正在確認進度")

    result = compute_auto_reply(ProgressSnapshot("in_progress", None, "剩國語"), defaults)

    # 無 ETA 時不帶 note，只回設定的文案
    assert result == AutoReply("in_progress", None, "老師正在確認進度", "auto", True)


def test_auto_reply_no_eta_auto_off() -> None:
    result = compute_auto_reply(ProgressSnapshot("in_progress", None, None), _AUTO_OFF)

    assert result == AutoReply("in_progress", None, None, None, True)
    assert compute_auto_reply(None, _AUTO_OFF) == AutoReply("not_started", None, None, None, True)


def test_auto_reply_truncate() -> None:
    result = compute_auto_reply(ProgressSnapshot("in_progress", time(17, 30), "字" * 300), _AUTO_ON)

    assert result.reply_message is not None
    assert len(result.reply_message) == 200
    assert result.reply_message.startswith("預計 17:30 可接送\n字")


def test_auto_reply_no_truncate_at_limit() -> None:
    prefix = "預計 17:30 可接送\n"
    note = "字" * (200 - len(prefix))

    result = compute_auto_reply(ProgressSnapshot("in_progress", time(17, 30), note), _AUTO_ON)

    assert result.reply_message == prefix + note
