"""schemas 共用基底。

- ``RequestModel``：request body / query 的基底，``extra='forbid'``、``str_strip_whitespace=True``。
- ``UpdateModel``：PATCH body 的基底，至少要給一個欄位；明確送 ``null`` 只允許
  ``nullable_fields`` 內的欄位（其餘欄位不可清空）。以 ``model_fields_set``
  區分「未給」與「給 null」。
- ``OutModel``：response 的基底，可由 ORM 物件建構（``from_attributes=True``）。
"""

from __future__ import annotations

from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, model_validator


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


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
