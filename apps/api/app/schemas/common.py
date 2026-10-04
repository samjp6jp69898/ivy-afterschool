"""schemas 共用基底。

- ``RequestModel``：request body / query 的基底，``extra='forbid'``、``str_strip_whitespace=True``，
  並拒絕含 NUL 字元的字串（含巢狀 list / dict）。
- ``UpdateModel``：PATCH body 的基底，至少要給一個欄位；明確送 ``null`` 只允許
  ``nullable_fields`` 內的欄位（其餘欄位不可清空）。以 ``model_fields_set``
  區分「未給」與「給 null」。
- ``OutModel``：response 的基底，可由 ORM 物件建構（``from_attributes=True``）。
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# PostgreSQL integer 上限；超過會在寫入時 DataError
SortOrder = Annotated[int, Field(ge=0, le=2147483647)]


def _contains_nul(value: Any) -> bool:
    """PostgreSQL text / jsonb 不接受 NUL；以明確 stack 走訪 str、bytes、list、dict（含 key）。

    不用 Python 遞迴（深層巢狀會 RecursionError）；以 id 去重，循環參照與共用子結構都只走一次。
    """
    stack = [value]
    seen: set[int] = set()
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if "\x00" in item:
                return True
        elif isinstance(item, bytes | bytearray):
            if b"\x00" in item:
                return True
        elif isinstance(item, dict | list | tuple):
            if id(item) in seen:
                continue
            seen.add(id(item))
            if isinstance(item, dict):
                stack.extend(item.keys())
                stack.extend(item.values())
            else:
                stack.extend(item)
    return False


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _reject_nul(cls, data: Any) -> Any:
        if _contains_nul(data):
            raise ValueError("不可包含 NUL 字元")
        return data


class UpdateModel(RequestModel):
    nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _require_a_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("至少要修改一個欄位")
        for name in self.model_fields_set:
            if getattr(self, name) is None and name not in self.nullable_fields:
                raise ValueError(f"{name} 不可為 null")
        return self


class OutModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
