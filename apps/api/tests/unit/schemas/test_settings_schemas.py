"""BACKEND-111：系統設定 schemas。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.settings import SettingOut, SettingPutIn, SettingsListOut


def test_settings_schemas_put_extra() -> None:
    with pytest.raises(ValidationError) as exc:
        SettingPutIn.model_validate({"value": {}, "key": "x"})
    assert exc.value.errors()[0]["type"] == "extra_forbidden"


def test_settings_schemas_put_value_object() -> None:
    with pytest.raises(ValidationError):
        SettingPutIn.model_validate({"value": [1, 2]})
    with pytest.raises(ValidationError):
        SettingPutIn.model_validate({})
    assert SettingPutIn.model_validate({"value": {"a": {"b": 1}}}).value == {"a": {"b": 1}}


def test_settings_schemas_out_and_list() -> None:
    item = SettingOut(
        key="org.profile",
        group="org",
        label="安親班資料",
        is_secret=False,
        value={"name": "常春藤"},
        json_schema={"type": "object"},
        updated_at=None,
        updated_by_name=None,
    )
    assert item.updated_at is None
    assert item.updated_by_name is None
    with_meta = item.model_copy(
        update={"updated_at": datetime(2026, 9, 1, tzinfo=UTC), "updated_by_name": "王老師"}
    )
    assert SettingsListOut(items=[with_meta]).model_dump()["items"][0]["updated_by_name"] == "王老師"
