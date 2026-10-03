"""部署設定的結構與行為測試（INFRA-033 起：web 服務的 nginx 範本）。

結構測試直接解析 apps/web/nginx/ 底下的檔案；行為測試以 docker 執行官方 nginx 映像套用本範本，
環境沒有 Docker 時 pytest.fail（不 skip）。

行為測試的記錄後端也是容器（nginx 映像以 `return 200` 回傳收到的 X-Forwarded-For），與被測的 nginx
放在同一個 user-defined network，以容器名稱互連（docker 內建 DNS 127.0.0.11）。這個做法不經過主機
loopback，也不依賴 host.docker.internal，Linux docker 與 Docker Desktop 的行為相同；主機只透過被測
nginx 綁在 127.0.0.1 的 port 發請求，記錄後端不對主機發佈任何 port。
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import time
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from conftest import RunCmd

REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_DIR = REPO_ROOT / "apps" / "web" / "nginx"
TEMPLATE = NGINX_DIR / "default.conf.template"
SECURITY_HEADERS = NGINX_DIR / "security-headers.conf"
REAL_IP_SCRIPT = NGINX_DIR / "40-real-ip.sh"
NGINX_IMAGE = "nginx:1.27-alpine"
SECURITY_INCLUDE = "include /etc/nginx/security-headers.conf"
STATIC_LOCATIONS = ("/", "= /index.html", "/parent/", "/assets/")


def _template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def location_block(conf: str, spec: str) -> str:
    """回傳 `location <spec> {` 區塊的內容（以大括號配對）；找不到時 raise KeyError。"""
    match = re.search(r"location\s+" + re.escape(spec) + r"\s*\{", conf)
    if match is None:
        raise KeyError(f"找不到 location {spec}")
    depth = 1
    start = match.end()
    for index in range(start, len(conf)):
        if conf[index] == "{":
            depth += 1
        elif conf[index] == "}":
            depth -= 1
            if depth == 0:
                return conf[start:index]
    raise KeyError(f"location {spec} 的大括號未配對")


def locations_missing_security_headers(conf: str) -> list[str]:
    """靜態內容 location 中沒有 include 安全標頭（或不存在）的清單。"""
    missing: list[str] = []
    for spec in STATIC_LOCATIONS:
        try:
            block = location_block(conf, spec)
        except KeyError:
            missing.append(spec)
            continue
        if SECURITY_INCLUDE not in block:
            missing.append(spec)
    return missing


def _csp() -> str:
    match = re.search(
        r'add_header\s+Content-Security-Policy\s+"([^"]+)"\s+always;',
        SECURITY_HEADERS.read_text(encoding="utf-8"),
    )
    assert match is not None, "security-headers.conf 沒有 Content-Security-Policy"
    return match.group(1)


def _csp_directive(csp: str, name: str) -> list[str]:
    for part in csp.split(";"):
        tokens = part.split()
        if tokens and tokens[0] == name:
            return tokens[1:]
    raise KeyError(name)


# ---------------------------------------------------------------------------
# 結構測試
# ---------------------------------------------------------------------------


def test_nginx_ws_location_upgrades() -> None:
    block = location_block(_template(), "/api/ws/")

    assert "proxy_set_header Upgrade $http_upgrade" in block
    assert 'proxy_set_header Connection "upgrade"' in block
    assert "proxy_http_version 1.1" in block
    assert "proxy_read_timeout 3600s" in block
    assert "proxy_send_timeout 3600s" in block


def test_nginx_api_location_body_limit() -> None:
    block = location_block(_template(), "/api/")

    assert "client_max_body_size 20m" in block
    assert "proxy_pass $backend_upstream" in block
    assert "proxy_read_timeout 60s" in block


def test_nginx_backend_upstream_resolved_per_request() -> None:
    conf = _template()

    assert "resolver ${NGINX_LOCAL_RESOLVERS} valid=10s ipv6=on;" in conf
    assert "set $backend_upstream ${BACKEND_URL};" in conf
    assert "proxy_pass $backend_upstream" in location_block(conf, "/api/ws/")


def test_nginx_healthz_location() -> None:
    block = location_block(_template(), "= /healthz")

    assert "return 200" in block


def test_nginx_static_locations_include_security_headers() -> None:
    assert locations_missing_security_headers(_template()) == []

    constructed = (
        "server {\n"
        f"  location / {{ {SECURITY_INCLUDE}; try_files $uri /index.html; }}\n"
        '  location = /index.html { add_header Cache-Control "no-cache" always; }\n'
        "  location /parent/ { try_files $uri /parent/index.html; }\n"
        f"  location /assets/ {{ {SECURITY_INCLUDE}; }}\n"
        "}\n"
    )
    assert locations_missing_security_headers(constructed) == ["= /index.html", "/parent/"]


def test_nginx_csp_directives() -> None:
    csp = _csp()

    assert "frame-ancestors 'none'" in csp
    assert "https://api.line.me" in csp
    assert "object-src 'none'" in csp
    assert "https://*.r2.cloudflarestorage.com" in _csp_directive(csp, "img-src")
    assert "'unsafe-eval'" not in csp
    assert "supabase" not in csp


def test_nginx_security_headers_always() -> None:
    lines = [
        line.strip()
        for line in SECURITY_HEADERS.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("add_header")
    ]

    assert len(lines) == 6
    assert all(line.endswith("always;") for line in lines)


def _run_real_ip(run_cmd: RunCmd, tmp_path: Path, cidrs: str) -> subprocess.CompletedProcess[str]:
    return run_cmd(
        ["/bin/sh", str(REAL_IP_SCRIPT)],
        env={"TRUSTED_EDGE_CIDRS": cidrs, "REAL_IP_CONF": str(tmp_path / "real-ip.conf")},
    )


def test_nginx_real_ip_config(run_cmd: RunCmd, tmp_path: Path) -> None:
    conf = _template()
    assert "include /etc/nginx/real-ip.conf;" in conf
    assert "real_ip_header X-Forwarded-For;" in conf
    assert "real_ip_recursive on;" in conf

    ok = _run_real_ip(run_cmd, tmp_path, "10.0.0.0/8 fd00::/8")
    assert ok.returncode == 0, ok.stderr
    lines = (tmp_path / "real-ip.conf").read_text(encoding="utf-8").splitlines()
    assert lines == ["set_real_ip_from 10.0.0.0/8;", "set_real_ip_from fd00::/8;"]

    for bad in ("", "1.2.3.4/8; evil"):
        (tmp_path / "real-ip.conf").unlink(missing_ok=True)
        result = _run_real_ip(run_cmd, tmp_path, bad)
        assert result.returncode == 1, (bad, result.stdout, result.stderr)
        assert not (tmp_path / "real-ip.conf").exists()


def test_nginx_overwrites_forwarded_for() -> None:
    conf = _template()

    for spec in ("/api/", "/api/ws/"):
        assert "proxy_set_header X-Forwarded-For $remote_addr" in location_block(conf, spec)
    assert "$proxy_add_x_forwarded_for" not in conf


# ---------------------------------------------------------------------------
# docker 行為測試
# ---------------------------------------------------------------------------


def _require_docker(run_cmd: RunCmd) -> None:
    if shutil.which("docker") is None or run_cmd(["docker", "info"], timeout=30).returncode != 0:
        pytest.fail("此測試需要 Docker")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def _nginx_mounts() -> list[str]:
    # 掛載來源不存在時 docker 會在主機上建立同名空目錄，且 nginx 會改用映像內建設定而假綠
    for path in (TEMPLATE, SECURITY_HEADERS, REAL_IP_SCRIPT):
        if not path.is_file():
            pytest.fail(f"缺少 {path.relative_to(REPO_ROOT)}")
    return [
        "-v",
        f"{TEMPLATE}:/etc/nginx/templates/default.conf.template:ro",
        "-v",
        f"{SECURITY_HEADERS}:/etc/nginx/security-headers.conf:ro",
        "-v",
        f"{REAL_IP_SCRIPT}:/docker-entrypoint.d/40-real-ip.sh:ro",
    ]


def test_nginx_config_syntax_valid(run_cmd: RunCmd) -> None:
    _require_docker(run_cmd)

    result = run_cmd(
        [
            "docker",
            "run",
            "--rm",
            "-e",
            "PORT=8080",
            "-e",
            "BACKEND_URL=http://127.0.0.1:9",
            "-e",
            "NGINX_ENTRYPOINT_LOCAL_RESOLVERS=true",
            "-e",
            "TRUSTED_EDGE_CIDRS=10.0.0.0/8",
            *_nginx_mounts(),
            NGINX_IMAGE,
            "nginx",
            "-T",
        ],
        timeout=180,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "syntax is ok" in result.stderr
    # -T 印出實際載入的設定：確認套用的是本範本與產生的 real-ip.conf，而非映像內建設定
    assert "location /api/ws/" in result.stdout
    assert "set_real_ip_from 10.0.0.0/8;" in result.stdout


_BACKEND_CONF = (
    "server { listen 8080; location / { default_type text/plain; "
    'return 200 "xff=[$http_x_forwarded_for]"; } }'
)


class _NginxProbe:
    """在同一個 user-defined network 上起「記錄後端」與「被測 nginx」兩個容器。"""

    def __init__(self, run_cmd: RunCmd, trusted: str) -> None:
        self.run_cmd = run_cmd
        suffix = uuid4().hex[:8]
        self.network = f"afterschool-nginx-probe-{suffix}"
        self.backend = f"afterschool-nginx-backend-{suffix}"
        self.web = f"afterschool-nginx-web-{suffix}"
        self.trusted = trusted
        self.web_port = _free_port()

    def _docker(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = self.run_cmd(["docker", *args], timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    def start(self) -> None:
        mounts = _nginx_mounts()
        self._docker("network", "create", self.network)
        self._docker(
            "run",
            "-d",
            "--rm",
            "--name",
            self.backend,
            "--network",
            self.network,
            NGINX_IMAGE,
            "sh",
            "-c",
            f"printf '%s\\n' '{_BACKEND_CONF}' > /etc/nginx/conf.d/default.conf "
            "&& exec nginx -g 'daemon off;'",
        )
        self._docker(
            "run",
            "-d",
            "--rm",
            "--name",
            self.web,
            "--network",
            self.network,
            "-p",
            f"127.0.0.1:{self.web_port}:8080",
            "-e",
            "PORT=8080",
            "-e",
            f"BACKEND_URL=http://{self.backend}:8080",
            "-e",
            "NGINX_ENTRYPOINT_LOCAL_RESOLVERS=true",
            "-e",
            f"TRUSTED_EDGE_CIDRS={self.trusted}",
            *mounts,
            NGINX_IMAGE,
        )

    def stop(self) -> None:
        self.run_cmd(["docker", "rm", "-f", self.web, self.backend], timeout=60)
        self.run_cmd(["docker", "network", "rm", self.network], timeout=60)

    def get(
        self, path: str, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], str]:
        """對被測 nginx 發請求；容器尚未就緒（連不上或後端 502）時重試，最多 30 秒。"""
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.web_port}{path}", headers=headers or {}
        )
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310  固定本機 URL
                    return response.status, dict(response.headers), response.read().decode()
            except OSError:
                if time.monotonic() > deadline:
                    logs = self.run_cmd(["docker", "logs", self.web])
                    pytest.fail(f"nginx 容器未就緒：{logs.stdout}{logs.stderr}")
                time.sleep(0.5)


StartProbe = Callable[[str], _NginxProbe]


@pytest.fixture
def start_probe(run_cmd: RunCmd) -> Iterator[StartProbe]:
    """回傳啟動函式：以指定的 TRUSTED_EDGE_CIDRS 起一組容器；測試結束一律移除容器與 network。"""
    _require_docker(run_cmd)
    started: list[_NginxProbe] = []

    def _start(trusted: str) -> _NginxProbe:
        probe = _NginxProbe(run_cmd, trusted)
        started.append(probe)
        probe.start()
        return probe

    try:
        yield _start
    finally:
        for probe in started:
            probe.stop()


def _forwarded_for_seen(start_probe: StartProbe, trusted: str) -> str:
    status, _, body = start_probe(trusted).get("/api/probe", headers={"X-Forwarded-For": "6.6.6.6"})
    assert status == 200, body
    assert body.startswith("xff=["), body
    assert body.endswith("]"), body
    return body.removeprefix("xff=[").removesuffix("]")


def test_nginx_spoofed_forwarded_for_not_passed(start_probe: StartProbe) -> None:
    untrusted = _forwarded_for_seen(start_probe, "203.0.113.0/24")
    assert untrusted != ""
    assert "," not in untrusted, untrusted
    assert "6.6.6.6" not in untrusted

    # 對照組：信任所有來源時，nginx 會採信用戶端自填值（證明測試能偵測信任設定）
    trusted = _forwarded_for_seen(start_probe, "0.0.0.0/0")
    assert trusted == "6.6.6.6"


def test_nginx_html_responses_carry_security_headers(start_probe: StartProbe) -> None:
    probe = start_probe("203.0.113.0/24")

    # / 與後台 SPA 路由都由 location = /index.html 回應（映像內建的 index.html）
    for path in ("/", "/students/1"):
        status, headers, _ = probe.get(path)
        assert status == 200, path
        assert "frame-ancestors 'none'" in headers.get("Content-Security-Policy", ""), path
        assert headers.get("X-Frame-Options") == "DENY", path
        assert headers.get("Cache-Control") == "no-cache", path
