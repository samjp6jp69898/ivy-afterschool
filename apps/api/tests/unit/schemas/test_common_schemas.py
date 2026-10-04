"""app/schemas/common.py：request / update / out 基底 model 的共同行為。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.common import OutModel, RequestModel, SortOrder, UpdateModel


class _Req(RequestModel):
    name: str


class _Upd(UpdateModel):
    name: str | None = None
    note: str | None = None

    nullable_fields = frozenset({"note"})


class _Out(OutModel):
    name: str


class _Orm:
    name = "王小明"


def test_common_schemas_request_strips_and_forbids_extra() -> None:
    assert _Req(name="  王小明 ").name == "王小明"
    with pytest.raises(ValidationError) as exc:
        _Req.model_validate({"name": "a", "x": 1})
    assert exc.value.errors()[0]["type"] == "extra_forbidden"


def test_common_schemas_update_requires_field() -> None:
    with pytest.raises(ValidationError, match="至少要修改一個欄位"):
        _Upd.model_validate({})
    assert _Upd.model_validate({"name": "a"}).model_fields_set == {"name"}


def test_common_schemas_update_null_only_for_nullable_fields() -> None:
    assert _Upd.model_validate({"note": None}).note is None
    with pytest.raises(ValidationError, match="不可為 null"):
        _Upd.model_validate({"name": None})


def test_common_schemas_out_from_attributes() -> None:
    assert _Out.model_validate(_Orm()).name == "王小明"


def test_common_schemas_request_rejects_nul_in_str_field() -> None:
    with pytest.raises(ValidationError, match="NUL"):
        _Req.model_validate({"name": "王\x00小明"})
    assert _Req.model_validate({"name": "王小明"}).name == "王小明"


def test_common_schemas_request_rejects_nul_in_list_str_element() -> None:
    class _ListReq(RequestModel):
        items: list[str]

    with pytest.raises(ValidationError, match="NUL"):
        _ListReq.model_validate({"items": ["ok", "a\x00b"]})
    assert _ListReq.model_validate({"items": ["ok"]}).items == ["ok"]


def test_common_schemas_sort_order_bounds() -> None:
    class _Sorted(RequestModel):
        sort_order: SortOrder

    assert _Sorted(sort_order=2147483647).sort_order == 2147483647
    for bad in (2147483648, -1):
        with pytest.raises(ValidationError):
            _Sorted(sort_order=bad)
