"""Alembic 執行環境（INFRA-045；architecture_decisions §5）。

- 連線只讀環境變數 `MIGRATION_DATABASE_URL`（owner）。執行期的 `DATABASE_URL`（app_backend）
  沒有 DDL 權限，不得作為退路。
- online：連線後先設 `lock_timeout`（DDL 等鎖有上限，逾時失敗而不是無限期卡住），再 commit 掉
  autobegin 的隱式交易才交給 `context.begin_transaction()`；否則 migration 會跑在從未 commit 的
  交易裡，連線關閉時整批 rollback（log 照印 Running upgrade，DB 卻沒有變）。
  session 級 `set_config` 在 commit 後仍有效。
- 全部待套用的 revision 在同一個交易內執行（forward-only，失敗整批不生效）。
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool, text

from app.models import Base

# 程式常數，不是 env：調整需改程式並經 review
MIGRATION_LOCK_TIMEOUT_MS = 10000

_SQLALCHEMY_SCHEME = "postgresql+psycopg://"
_PLAIN_SCHEME = "postgresql://"

config = context.config
if config.config_file_name is not None:
    # 不停用既有 logger：測試在同一行程內呼叫 command.upgrade 時，避免關掉 pytest 的 logging
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def migration_url() -> str:
    url = os.environ.get("MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "未設定 MIGRATION_DATABASE_URL（migration 以 owner 連線執行，不使用 DATABASE_URL）"
        )
    if url.startswith(_PLAIN_SCHEME):
        return _SQLALCHEMY_SCHEME + url.removeprefix(_PLAIN_SCHEME)
    return url


def apply_lock_timeout(connection: Connection) -> None:
    # SET 不接受 bind 參數，改用 set_config（第三個參數 false = session 級）
    connection.execute(
        text("select set_config('lock_timeout', :ms, false)"),
        {"ms": str(MIGRATION_LOCK_TIMEOUT_MS)},
    )


def run_migrations_offline() -> None:
    context.configure(
        url=migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(migration_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        apply_lock_timeout(connection)
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
