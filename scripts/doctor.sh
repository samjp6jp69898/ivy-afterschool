#!/usr/bin/env bash
# 本機開發環境健康檢查（INFRA-018）：just doctor
#
# 每項印一行 OK / WARN / FAIL + 名稱 + 修復提示；exit code = FAIL 項數（上限 125）。
# WARN 不計入 exit code（port 被占用、Supabase 未啟動、service role key 未設定）。
#
# 用法：
#   just doctor            檢查本機環境
#   just doctor --help     顯示本說明
#
# 環境變數（測試用）：
#   DOCTOR_ROOT       覆寫 repo root
#   DOCTOR_API_PORT   API port（預設 8341）
#   DOCTOR_WEB_PORT   Web port（預設 5341）
#
# 設計取捨：不用 `set -e`，一次列完所有問題；port 探測用 python3 socket（macOS 的 nc -w
# 不套用在 connect 上，被過濾的位址會卡到 TCP 預設逾時）。

set -uo pipefail

for arg in "$@"; do
    case "$arg" in
        -h | --help)
            sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "未知參數：${arg}（只接受 --help）" >&2
            exit 1
            ;;
    esac
done

if [ -n "${DOCTOR_ROOT:-}" ]; then
    ROOT="$DOCTOR_ROOT"
else
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
API_PORT="${DOCTOR_API_PORT:-8341}"
WEB_PORT="${DOCTOR_WEB_PORT:-5341}"

FAIL_COUNT=0
WARN_COUNT=0

ok() { printf 'OK    %s\n' "$1"; }
warn() {
    printf 'WARN  %s → %s\n' "$1" "$2"
    WARN_COUNT=$((WARN_COUNT + 1))
}
fail() {
    printf 'FAIL  %s → %s\n' "$1" "$2"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

# 印出版本字串中的主版號（第一段連續數字）；取不到時印空字串。
major_of() {
    printf '%s' "$1" | sed -n 's/^[^0-9]*\([0-9][0-9]*\).*/\1/p' | head -n 1
}

# 只讀檔案內容，不 source，避免污染 doctor 自己的環境。
read_env_value() {
    local env_file="$1" key="$2" line
    line="$(grep -E "^${key}=" "$env_file" 2>/dev/null | tail -n 1)"
    printf '%s' "${line#*=}"
}

tcp_open() {
    python3 - "$1" "$2" <<'PY' 2>/dev/null
import socket
import sys

try:
    socket.create_connection((sys.argv[1], int(sys.argv[2])), 1).close()
except OSError:
    sys.exit(1)
PY
}

# 檢查工具在 PATH 且主版號符合條件。$1=名稱 $2=比較（eq / ge）$3=版本 $4=提示
check_tool_version() {
    local name="$1" op="$2" want="$3" hint="$4" version major
    if ! command -v "$name" >/dev/null 2>&1; then
        fail "$name" "不在 PATH；${hint}"
        return
    fi
    version="$("$name" --version 2>/dev/null | head -n 1)"
    major="$(major_of "$version")"
    if [ -z "$major" ]; then
        fail "$name" "無法解析版本（${version:-無輸出}）；${hint}"
    elif [ "$op" = "eq" ] && [ "$major" -ne "$want" ]; then
        fail "$name" "需要主版號 ${want}，目前 ${version}；${hint}"
    elif [ "$op" = "ge" ] && [ "$major" -lt "$want" ]; then
        fail "$name" "需要主版號 >= ${want}，目前 ${version}；${hint}"
    else
        ok "$name ${version}"
    fi
}

# --- 工具 -------------------------------------------------------------------

if command -v just >/dev/null 2>&1; then
    ok "just"
else
    fail "just" "不在 PATH；brew install just"
fi

if command -v uv >/dev/null 2>&1; then
    ok "uv"
    if uv python find 3.13 >/dev/null 2>&1; then
        ok "Python 3.13"
    else
        fail "Python 3.13" "uv 找不到 Python 3.13；uv python install 3.13"
    fi
else
    fail "uv" "不在 PATH；安裝 uv（https://docs.astral.sh/uv/）"
    fail "Python 3.13" "需要 uv 才能檢查；先安裝 uv 再 uv python install 3.13"
fi

check_tool_version node eq 24 "安裝 Node 24"
check_tool_version pnpm ge 10 "corepack enable"
check_tool_version supabase ge 2 "安裝 Supabase CLI"

if ! command -v docker >/dev/null 2>&1; then
    fail "docker daemon" "docker 不在 PATH；安裝並啟動 Docker Desktop"
elif docker info >/dev/null 2>&1; then
    ok "docker daemon"
else
    fail "docker daemon" "docker info 失敗；啟動 Docker Desktop"
fi

# --- 專案目錄與 .env ---------------------------------------------------------

if [ -d "$ROOT/apps/api/.venv" ]; then
    ok "apps/api/.venv"
else
    fail "apps/api/.venv" "不存在；just bootstrap"
fi

if [ -d "$ROOT/apps/web/node_modules" ]; then
    ok "apps/web/node_modules"
else
    fail "apps/web/node_modules" "不存在；just bootstrap"
fi

API_ENV="$ROOT/apps/api/.env"
if [ ! -f "$API_ENV" ]; then
    fail "apps/api/.env" "不存在；just bootstrap"
else
    secret="$(read_env_value "$API_ENV" APP_SECRET_KEY)"
    if [ "$secret" = "change-me" ] || [ "${#secret}" -lt 32 ]; then
        fail "apps/api/.env 的 APP_SECRET_KEY" "仍是 change-me 或長度 < 32；just bootstrap 或手動產生"
    else
        ok "apps/api/.env 的 APP_SECRET_KEY"
    fi
    service_key="$(read_env_value "$API_ENV" SUPABASE_SERVICE_ROLE_KEY)"
    if [ "$service_key" = "change-me" ]; then
        warn "apps/api/.env 的 SUPABASE_SERVICE_ROLE_KEY" "仍是 change-me，Storage 功能無法使用；啟動 Supabase 後重跑 just bootstrap"
    else
        ok "apps/api/.env 的 SUPABASE_SERVICE_ROLE_KEY"
    fi
fi

# --- port ------------------------------------------------------------------

# var/run/*.pid 記錄的、仍存活的行程 pid（本專案 dev server 自己占用的 port 不算衝突）
own_pids() {
    local f pid
    for f in "$ROOT"/var/run/*.pid; do
        [ -f "$f" ] || continue
        pid="$(head -n 1 "$f" | tr -dc '0-9')"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            printf '%s\n' "$pid"
        fi
    done
}

listener_pids() {
    local lsof_bin
    lsof_bin="$(command -v lsof 2>/dev/null || true)"
    [ -z "$lsof_bin" ] && [ -x /usr/sbin/lsof ] && lsof_bin=/usr/sbin/lsof
    [ -z "$lsof_bin" ] && return 1
    "$lsof_bin" -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null
    return 0
}

check_app_port() {
    local label="$1" port="$2" mine listeners pid
    if ! tcp_open 127.0.0.1 "$port"; then
        ok "port ${port}（${label}）可用"
        return
    fi
    mine="$(own_pids)"
    if [ -n "$mine" ]; then
        if listeners="$(listener_pids "$port")"; then
            for pid in $listeners; do
                if printf '%s\n' "$mine" | grep -qx "$pid"; then
                    ok "port ${port}（${label}）由本專案服務使用中"
                    return
                fi
            done
        else
            # 沒有 lsof 無法比對 pid，有存活的 pid 檔就視為本專案服務
            ok "port ${port}（${label}）由本專案服務使用中"
            return
        fi
    fi
    warn "port ${port}（${label}）被其他程式占用" "關閉占用程式（lsof -nP -iTCP:${port} -sTCP:LISTEN）"
}

check_app_port "API" "$API_PORT"
check_app_port "Web" "$WEB_PORT"

for port in 54341 54342; do
    if tcp_open 127.0.0.1 "$port"; then
        ok "Supabase port ${port}"
    else
        warn "Supabase port ${port} 無法連線" "just db-start"
    fi
done

printf '\n失敗 %d 項、警告 %d 項。\n' "$FAIL_COUNT" "$WARN_COUNT"
if [ "$FAIL_COUNT" -gt 125 ]; then
    exit 125
fi
exit "$FAIL_COUNT"
