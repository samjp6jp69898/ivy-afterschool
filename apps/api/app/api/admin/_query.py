"""後台列表 endpoint 共用：把 query string 驗證成 ``RequestModel``（extra='forbid'）。

FastAPI 的 ``Annotated[Model, Query()]`` 會把 page / page_size 也視為未知參數而 422，所以這裡先排除
分頁參數再驗證；驗證失敗轉成與其他 422 相同的 ``RequestValidationError``（loc 前綴 ``query``）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.schemas.common import RequestModel

PAGINATION_PARAMS: Final = frozenset({"page", "page_size"})


def query_model[M: RequestModel](model: type[M]) -> Callable[[Request], M]:
    def dependency(request: Request) -> M:
        raw = {k: v for k, v in request.query_params.items() if k not in PAGINATION_PARAMS}
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            errors = [{**err, "loc": ("query", *err["loc"])} for err in exc.errors()]
            raise RequestValidationError(errors) from None

    return dependency
