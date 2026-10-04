"""BACKEND-019：app/core/security_middleware.py（Origin 檢查與安全標頭）。"""

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.responses import Response
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.core.security_middleware import SecurityMiddleware, allowed_origins

_ALLOWED = "http://127.0.0.1:5341"
_PUBLIC = "https://afterschool.example.com"
_EVIL = "https://evil.test"
_LOCAL_R2_SECRET = "afterschool-local-secret"  # noqa: S105  本機 SeaweedFS 固定開發值


def _settings(cors_origins: str = _ALLOWED, public_base_url: str = _PUBLIC) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+psycopg://u:p@127.0.0.1:54342/postgres",
        app_secret_key="s" * 48,
        cors_origins=cors_origins,  # validator 會把逗號字串切成 list
        public_base_url=public_base_url,
        r2_endpoint_url="http://127.0.0.1:54344",
        r2_access_key_id="afterschool",
        r2_secret_access_key=_LOCAL_R2_SECRET,
        r2_bucket="afterschool-local",
    )


def _client(settings: Settings | None = None) -> TestClient:
    app = FastAPI()
    app.add_middleware(SecurityMiddleware, settings=settings or _settings())

    @app.get("/api/x")
    def _get() -> dict[str, str]:
        return {"ok": "get"}

    @app.post("/api/x")
    def _post() -> dict[str, str]:
        return {"ok": "post"}

    @app.put("/api/x")
    def _put() -> dict[str, str]:
        return {"ok": "put"}

    @app.get("/docs-like")
    def _non_api() -> dict[str, str]:
        return {"ok": "non-api"}

    @app.websocket("/ws")
    async def _ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"ok": "ws"})
        await websocket.close()

    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    return _client()


# --- HTTP Origin / Referer --------------------------------------------------------------


def test_security_mw_foreign_origin_post_403(client: TestClient) -> None:
    response = client.post("/api/x", headers={"Origin": _EVIL})

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "origin_forbidden"
    assert set(body["error"]) == {"code", "message", "details"}
    assert _EVIL not in response.text

    for method in ("put", "patch", "delete"):
        assert client.request(method, "/api/x", headers={"Origin": _EVIL}).status_code == 403


def test_security_mw_allowed_origin_post(client: TestClient) -> None:
    assert client.post("/api/x", headers={"Origin": _ALLOWED}).json() == {"ok": "post"}
    assert client.post("/api/x", headers={"Origin": _PUBLIC}).json() == {"ok": "post"}
    # 預設 port 正規化：:443 與不寫 port 視為同一來源；大小寫不敏感
    assert client.post("/api/x", headers={"Origin": _PUBLIC + ":443"}).status_code == 200
    assert client.post("/api/x", headers={"Origin": _PUBLIC.upper()}).status_code == 200
    # 同主機不同 port 不算同一來源
    assert client.post("/api/x", headers={"Origin": "http://127.0.0.1:5342"}).status_code == 403
    assert client.put("/api/x", headers={"Origin": _ALLOWED}).json() == {"ok": "put"}


def test_security_mw_no_origin_passes(client: TestClient) -> None:
    assert client.post("/api/x").status_code == 200

    foreign_referer = client.post("/api/x", headers={"Referer": _EVIL + "/page?x=1"})
    assert foreign_referer.status_code == 403
    assert foreign_referer.json()["error"]["code"] == "origin_forbidden"

    assert (
        client.post("/api/x", headers={"Referer": _ALLOWED + "/admin/students"}).status_code == 200
    )
    # Origin 優先於 Referer
    assert (
        client.post("/api/x", headers={"Origin": _ALLOWED, "Referer": _EVIL + "/p"}).status_code
        == 200
    )
    assert client.post("/api/x", headers={"Referer": "not a url"}).status_code == 403


def test_security_mw_get_unaffected(client: TestClient) -> None:
    response = client.get("/api/x", headers={"Origin": _EVIL})

    assert response.status_code == 200
    assert response.json() == {"ok": "get"}
    # 沒有 HEAD 路由 → 405（由路由層回應，不是本 middleware 的 403）
    assert client.head("/api/x", headers={"Origin": _EVIL}).status_code == 405


def test_security_mw_allowed_origins_from_settings() -> None:
    settings = _settings(
        cors_origins=" http://a.test:8080 , https://b.test ", public_base_url=_PUBLIC
    )

    assert allowed_origins(settings) == frozenset(
        {"http://a.test:8080", "https://b.test", "https://afterschool.example.com"}
    )
    assert allowed_origins(_settings(public_base_url="http://localhost:5341/")) >= {
        "http://localhost:5341"
    }


# --- WebSocket --------------------------------------------------------------------------


def test_security_mw_ws_foreign_origin_closed(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/ws", headers={"origin": _EVIL}),
    ):
        pass

    assert exc_info.value.code == 4403


def test_security_mw_ws_allowed_or_missing_origin(client: TestClient) -> None:
    with client.websocket_connect("/ws", headers={"origin": _ALLOWED}) as ws:
        assert ws.receive_json() == {"ok": "ws"}
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json() == {"ok": "ws"}


# --- 安全標頭 ---------------------------------------------------------------------------


def test_security_mw_headers(client: TestClient) -> None:
    response = client.get("/api/x")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["cache-control"] == "no-store"

    forbidden = client.post("/api/x", headers={"Origin": _EVIL})
    assert forbidden.headers["x-content-type-options"] == "nosniff"
    assert forbidden.headers["cache-control"] == "no-store"

    non_api = client.get("/docs-like")
    assert non_api.headers["x-content-type-options"] == "nosniff"
    assert non_api.headers["x-frame-options"] == "DENY"
    assert "cache-control" not in non_api.headers


def test_security_mw_public_cacheable_path_keeps_handler_cache_control() -> None:
    app = FastAPI()
    app.add_middleware(SecurityMiddleware, settings=_settings())

    @app.get("/api/parent/config")
    def _public(response: Response) -> dict[str, str]:
        response.headers["Cache-Control"] = "public, max-age=60"
        return {"ok": "public"}

    @app.get("/api/private")
    def _private(response: Response) -> dict[str, str]:
        # 非公開路徑：即使 handler 想快取也一律 no-store
        response.headers["Cache-Control"] = "public, max-age=60"
        return {"ok": "private"}

    client = TestClient(app)

    public = client.get("/api/parent/config")
    private = client.get("/api/private")

    assert public.headers["cache-control"] == "public, max-age=60"
    assert public.headers["x-content-type-options"] == "nosniff"
    assert private.headers["cache-control"] == "no-store"
