"""BACKEND-063：家長帳號服務（get_me：家長資料與已綁定小孩清單）。

供 ``/api/parent/me``、liff-login、bind、refresh 的回應共用。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentParent
from app.core.clock import Clock
from app.core.errors import UnauthenticatedError
from app.core.storage import Storage
from app.models.parents import ParentAccount
from app.schemas.parent_children import ParentMeOut
from app.services.parent_children_service import list_children


def get_me(
    session: Session, *, parent: CurrentParent, storage: Storage, clock: Clock
) -> ParentMeOut:
    account = session.execute(
        select(ParentAccount).where(ParentAccount.id == parent.id)
    ).scalar_one_or_none()
    if account is None:  # token 有效但帳號已不存在
        raise UnauthenticatedError
    return ParentMeOut(
        id=account.id,
        display_name=account.display_name,
        picture_url=account.picture_url,
        phone=account.phone,
        children=list_children(session, account.id, storage=storage),
    )
