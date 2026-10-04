"""BACKEND-017：交易級 advisory lock（背景工作冪等 BACKEND-018、批次操作互斥 BACKEND-158）。

- ``advisory_key(namespace, key)``：``md5(f"{namespace}|{key}")`` 前 8 bytes 遮掉最高位 →
  0 < k < 2**63 的 bigint，供 ``pg_(try_)advisory_xact_lock(bigint)`` 使用。
- ``try_advisory_xact_lock``：非阻塞，取不到立刻回 False；``advisory_xact_lock``：阻塞直到取得。
  兩者都是 transaction 級：commit / rollback 自動釋放，不需手動 unlock，行程中斷也不留孤鎖。
- namespace 慣例 ``job:<job_id>``、``students:promote_grade``，只接受 ``^[a-z_:.]+$``。

移植 ivy ``utils/advisory_lock.py::try_scheduler_lock`` 的 key 產生方式；去掉 tenant 與 SQLite
分支。
"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

_NAMESPACE_RE = re.compile(r"^[a-z_:.]+$")
_MASK_63_BITS = 0x7FFF_FFFF_FFFF_FFFF


def advisory_key(namespace: str, key: str) -> int:
    if not _NAMESPACE_RE.fullmatch(namespace):
        raise ValueError(f"advisory lock namespace 只接受 ^[a-z_:.]+$：{namespace!r}")
    digest = hashlib.md5(f"{namespace}|{key}".encode(), usedforsecurity=False).digest()
    return int.from_bytes(digest[:8], "big") & _MASK_63_BITS


def try_advisory_xact_lock(session: Session, namespace: str, key: str) -> bool:
    k = advisory_key(namespace, key)
    return bool(session.execute(text("select pg_try_advisory_xact_lock(:k)"), {"k": k}).scalar())


def advisory_xact_lock(session: Session, namespace: str, key: str) -> None:
    k = advisory_key(namespace, key)
    session.execute(text("select pg_advisory_xact_lock(:k)"), {"k": k})
