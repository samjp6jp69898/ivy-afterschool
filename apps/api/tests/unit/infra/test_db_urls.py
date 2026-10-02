"""INFRA-041：tests/support/db_urls.py 的測試用 DB URL 解析與 loopback 守衛。

守衛是純函式：不連線、不碰 DB。環境變數相關的案例一律以 environ 參數或 monkeypatch 注入，
不依賴執行機器的真實環境。
"""

from collections.abc import Mapping

import pytest

from tests.support import db_urls

LOCAL_URL = "postgresql+psycopg://u:p@127.0.0.1:54342/postgres"
_ENV_KEYS = ("TEST_DB_BACKEND_URL", "TEST_DB_OWNER_URL", "PGHOST", "PGHOSTADDR", "PGSERVICE")


@pytest.fixture(autouse=True)
def _clean_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_db_urls_defaults() -> None:
    assert (
        db_urls.backend_url()
        == "postgresql+psycopg://app_backend:app_backend_local@127.0.0.1:54342/postgres"
    )
    assert db_urls.owner_url() == "postgresql+psycopg://postgres:postgres@127.0.0.1:54342/postgres"


def test_db_urls_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_DB_BACKEND_URL", "postgresql://a:b@127.0.0.1:54342/postgres")

    assert db_urls.backend_url() == "postgresql+psycopg://a:b@127.0.0.1:54342/postgres"


def test_db_urls_rejects_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="db.example.com"):
        db_urls.assert_loopback("postgresql+psycopg://u:p@db.example.com:5432/postgres")

    monkeypatch.setenv("TEST_DB_OWNER_URL", "postgresql+psycopg://postgres:x@10.0.0.5:5432/postgres")
    with pytest.raises(ValueError, match="10.0.0.5"):
        db_urls.owner_url()


@pytest.mark.parametrize(
    ("url", "environ", "expected"),
    [
        (LOCAL_URL + "?host=10.0.0.6", {}, "host=10.0.0.6"),
        (LOCAL_URL + "?hostaddr=10.0.0.7", {}, "hostaddr=10.0.0.7"),
        ("postgresql://u:p@127.0.0.1:54342,10.0.0.10:54342/postgres", {}, "host=10.0.0.10"),
        (LOCAL_URL, {"PGHOSTADDR": "10.0.0.8"}, "PGHOSTADDR=10.0.0.8"),
        ("postgresql://u:p@:54342/postgres", {"PGHOST": "10.0.0.9"}, "PGHOST=10.0.0.9"),
        (LOCAL_URL + "?service=remote", {}, "service=remote"),
        (LOCAL_URL, {"PGSERVICE": "remote"}, "PGSERVICE=remote"),
        ("postgresql://u:p@:54342/postgres", {}, "未指定 host"),
    ],
    ids=[
        "query-host",
        "query-hostaddr",
        "multi-host",
        "env-hostaddr",
        "env-host",
        "query-service",
        "env-service",
        "missing-host",
    ],
)
def test_db_urls_rejects_libpq_overrides(
    url: str, environ: Mapping[str, str], expected: str
) -> None:
    with pytest.raises(ValueError, match="只允許本機 loopback DB") as exc_info:
        db_urls.assert_loopback(url, environ)

    assert expected in str(exc_info.value)


def test_db_urls_rejects_libpq_overrides_via_backend_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_DB_BACKEND_URL", LOCAL_URL + "?hostaddr=10.0.0.7")

    with pytest.raises(ValueError, match="hostaddr=10.0.0.7"):
        db_urls.backend_url()


@pytest.mark.parametrize(
    ("url", "environ"),
    [
        (LOCAL_URL + "?hostaddr=127.0.0.1", {}),
        ("postgresql://u:p@localhost:54342/postgres", {}),
        # URL 已指定 host 時，libpq 不會再看 PGHOST
        (LOCAL_URL, {"PGHOST": "10.0.0.9"}),
    ],
    ids=["query-hostaddr-loopback", "localhost", "env-host-ignored-when-url-has-host"],
)
def test_db_urls_accepts_loopback_variants(url: str, environ: Mapping[str, str]) -> None:
    db_urls.assert_loopback(url, environ)


def test_db_urls_assert_connected_loopback() -> None:
    db_urls.assert_connected_loopback("127.0.0.1")
    db_urls.assert_connected_loopback("::1")

    with pytest.raises(ValueError, match="10.0.0.11"):
        db_urls.assert_connected_loopback("10.0.0.11")
    with pytest.raises(ValueError, match="只允許本機 loopback DB"):
        db_urls.assert_connected_loopback("")


def test_db_urls_accepts_ipv6_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    db_urls.assert_loopback("postgresql://u:p@[::1]:54342/postgres")

    monkeypatch.setenv("TEST_DB_BACKEND_URL", "postgresql://u:p@[::1]:54342/postgres")
    assert db_urls.backend_url() == "postgresql+psycopg://u:p@[::1]:54342/postgres"


def test_db_urls_to_psycopg_dsn() -> None:
    assert (
        db_urls.to_psycopg_dsn("postgresql+psycopg://a:b@127.0.0.1:54342/postgres")
        == "postgresql://a:b@127.0.0.1:54342/postgres"
    )
    # 已是 psycopg 形式則原樣回傳
    assert (
        db_urls.to_psycopg_dsn("postgresql://a:b@127.0.0.1:54342/postgres")
        == "postgresql://a:b@127.0.0.1:54342/postgres"
    )
