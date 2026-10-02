"""BACKEND-372：由作業項目狀態推導整體進度（domain_spec M6）。"""

import pytest

from app.services.homework_rules import derive_overall_status


@pytest.mark.parametrize(
    ("item_statuses", "expected"),
    [
        ([], "not_started"),
        (["todo", "todo"], "not_started"),
        (["done", "done"], "done"),
        (["done"], "done"),
        (["todo", "done"], "in_progress"),
        (["correcting"], "in_progress"),
        (["doing"], "in_progress"),
        (["doing", "done"], "in_progress"),
        (["todo", "doing"], "in_progress"),
        (["todo", "correcting", "done"], "in_progress"),
    ],
)
def test_derive_overall_status_cases(item_statuses: list[str], expected: str) -> None:
    assert derive_overall_status(item_statuses) == expected


def test_derive_overall_status_accepts_tuple() -> None:
    assert derive_overall_status(("done", "done")) == "done"


def test_derive_overall_status_unknown() -> None:
    with pytest.raises(ValueError, match="finished"):
        derive_overall_status(["finished"])
    with pytest.raises(ValueError, match="DONE"):
        derive_overall_status(["done", "DONE"])
