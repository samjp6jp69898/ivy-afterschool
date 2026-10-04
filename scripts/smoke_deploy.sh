#!/usr/bin/env bash
# 部署後煙霧測試（INFRA-039）：scripts/smoke_deploy.sh <base_url>（或 just smoke <base_url>）
#
# 對正式網址做最低限度驗證，每項印 `OK <名稱>` / `FAIL <名稱>（原因）`，exit code = FAIL 數（上限 125）。
# base_url 必須是 https://；http:// 只允許 host 為 127.0.0.1 / localhost（本機驗證 web 容器），
# 否則 exit 2。curl 一律 --max-time 10。macOS 內建 bash 3.2 相容。

set -uo pipefail

if [ $# -ne 1 ] || [ -z "$1" ]; then
    echo "用法: scripts/smoke_deploy.sh <base_url>（例如 https://example.up.railway.app）" >&2
    exit 1
fi

BASE="${1%/}"
case "$BASE" in
    https://*) SCHEME=https ;;
    http://*) SCHEME=http ;;
    *)
        echo "錯誤：base_url 必須以 https:// 開頭：$1" >&2
        exit 2
        ;;
esac

authority="${BASE#*://}"
authority="${authority%%/*}"
host="${authority%:*}"
if [ "$SCHEME" = http ] && { [ "$host" != "127.0.0.1" ] && [ "$host" != "localhost" ]; }; then
    echo "錯誤：base_url 必須是 https://（http:// 只允許 127.0.0.1 / localhost）：$1" >&2
    exit 2
fi
case "$authority" in
    *@*)
        echo "錯誤：base_url 不得含帳號資訊：$1" >&2
        exit 2
        ;;
esac

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILS=0

pass() { echo "OK   $1"; }
fail() {
    echo "FAIL $1（$2）"
    FAILS=$((FAILS + 1))
}

# fetch <path>：回應狀態碼寫入 $STATUS，標頭在 $TMP/headers，body 在 $TMP/body；連線失敗時 STATUS=000。
fetch() {
    STATUS="$(curl -sS --max-time 10 -o "$TMP/body" -D "$TMP/headers" -w '%{http_code}' "$BASE$1" 2>/dev/null)" || STATUS=000
    [ -n "$STATUS" ] || STATUS=000
}

check_status() { # <path> <預期狀態碼>
    fetch "$1"
    if [ "$STATUS" = "$2" ]; then
        pass "GET $1 → $2"
    else
        fail "GET $1" "預期 $2，實際 $STATUS"
    fi
}

check_spa() { # <path>
    fetch "$1"
    if [ "$STATUS" != 200 ]; then
        fail "GET $1" "預期 200，實際 $STATUS"
    elif ! grep -q 'id="app"' "$TMP/body"; then
        fail "GET $1" "body 不含 id=\"app\""
    else
        pass "GET $1 → 200 含 id=\"app\""
    fi
}

check_status /healthz 200
check_spa /
check_spa /parent/
check_status /api/health 200
check_status /api/admin/auth/me 401

# 安全標頭（取 GET / 的回應）
fetch /
required="Content-Security-Policy X-Frame-Options X-Content-Type-Options"
[ "$SCHEME" = https ] && required="$required Strict-Transport-Security"
missing=""
for name in $required; do
    grep -qi "^${name}:" "$TMP/headers" || missing="$missing $name"
done
if [ -z "$missing" ]; then
    pass "GET / 安全標頭齊全"
else
    fail "GET / 安全標頭" "缺少${missing}"
fi

check_status /assets/smoke-nonexistent.js.map 404

if [ "$FAILS" -gt 125 ]; then FAILS=125; fi
exit "$FAILS"
