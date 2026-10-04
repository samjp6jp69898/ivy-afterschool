#!/usr/bin/env bash
# 一鍵啟動本機開發環境（INFRA-016）：just up
#
# 依序：(1) 本機 DB / SeaweedFS（just db-start，docker compose up --wait 本身冪等）
#       (2) API（預設 just api，port 8341）(3) Web（預設 just web，port 5341）
# 背景服務以 nohup 啟動且自成 process group（pgid == pid），pid / 指令 / log 寫在 var/run/<name>.pid、var/run/<name>.cmd、var/log/<name>.log。
# pid 存活、命令列與 .cmd 相同且 port 可連線時視為已在執行、略過；port 未開則照常輪詢；
# pid 已死或被其他程式重用時重新啟動。
# 啟動後輪詢 port 最多 DEV_UP_WAIT_SECONDS 秒；服務提早結束或逾時 → 印 log 最後 20 行到 stderr 並 exit 1。
#
# 測試用覆寫：DEV_UP_ROOT、DEV_UP_API_CMD、DEV_UP_WEB_CMD、DEV_UP_API_PORT、DEV_UP_WEB_PORT、
#             DEV_UP_SKIP_DB（=1 略過 DB）、DEV_UP_WAIT_SECONDS。macOS 內建 bash 3.2 相容。

set -euo pipefail

if [ -n "${DEV_UP_ROOT:-}" ]; then
    ROOT="$DEV_UP_ROOT"
else
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
RUN_DIR="$ROOT/var/run"
LOG_DIR="$ROOT/var/log"
WAIT_SECONDS="${DEV_UP_WAIT_SECONDS:-30}"
API_CMD="${DEV_UP_API_CMD:-just api}"
WEB_CMD="${DEV_UP_WEB_CMD:-just web}"
API_PORT="${DEV_UP_API_PORT:-8341}"
WEB_PORT="${DEV_UP_WEB_PORT:-5341}"

mkdir -p "$RUN_DIR" "$LOG_DIR"

port_open() {
    python3 -c 'import socket, sys; socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1).close()' \
        "$1" 2>/dev/null
}

is_alive() {
    kill -0 "$1" 2>/dev/null
}

# ps 顯示的命令列與記錄的指令相符（一律整串相等，不用結尾比對）：
# (1) 完全相同；(2) ps 去掉第一個字後等於記錄指令（shebang 腳本前面多一個直譯器）；
# (3) 記錄指令的第一個字是直譯器（python* / node）時，雙方去掉第一個字後相等
#     （直譯器被換成實際執行檔，例如 venv 的 python 在 ps 顯示為框架內的 Python.app）。
command_matches() {
    local pid="$1" recorded="$2" actual first
    actual="$(ps -o command= -p "$pid" 2>/dev/null | sed -e 's/[[:space:]]*$//')"
    [ -n "$actual" ] || return 1
    [ "$actual" = "$recorded" ] && return 0
    [ "${actual#* }" = "$recorded" ] && return 0
    first="${recorded%% *}"
    case "${first##*/}" in
        python | python[0-9]* | node)
            [ "${recorded#* }" != "$recorded" ] && [ "${actual#* }" = "${recorded#* }" ] && return 0
            ;;
    esac
    return 1
}

fail_with_log() {
    local name="$1" port="$2" reason="$3" log="$LOG_DIR/$1.log"
    echo "錯誤：${name} ${reason}（port ${port}）。" >&2
    if [ -f "$log" ]; then
        echo "----- ${log} 最後 20 行 -----" >&2
        tail -n 20 "$log" >&2
    fi
    exit 1
}

# 輪詢 port 直到可連線；行程提早結束或逾時 → 印 log 並 exit 1
wait_ready() {
    local name="$1" pid="$2" port="$3" waited=0
    while ! port_open "$port"; do
        if ! is_alive "$pid"; then
            fail_with_log "$name" "$port" "啟動後立即結束"
        fi
        if [ "$waited" -ge "$WAIT_SECONDS" ]; then
            fail_with_log "$name" "$port" "在 ${WAIT_SECONDS} 秒內沒有開始監聽"
        fi
        sleep 1
        waited=$((waited + 1))
    done
    echo "    ${name} 已就緒（pid ${pid}）"
}

start_service() {
    local name="$1" cmd="$2" port="$3"
    local pid_file="$RUN_DIR/$name.pid" cmd_file="$RUN_DIR/$name.cmd" log="$LOG_DIR/$name.log" pid=""

    if [ -f "$pid_file" ]; then
        pid="$(tr -dc '0-9' < "$pid_file")"
        if [ -n "$pid" ] && is_alive "$pid" && [ -f "$cmd_file" ] \
            && [ "$(cat "$cmd_file")" = "$cmd" ] && command_matches "$pid" "$cmd"; then
            if port_open "$port"; then
                echo "    ${name} 已在執行（pid ${pid}）"
                return 0
            fi
            # 自己的行程還在但 port 沒開（例如上次逾時留下的）：不重啟，照常輪詢，逾時失敗
            echo "    ${name} 行程已存在（pid ${pid}），等待 port ${port}"
            wait_ready "$name" "$pid" "$port"
            return 0
        fi
        rm -f "$pid_file" "$cmd_file"
    fi

    # port 已被其他行程占用時，新啟動的服務會 bind 失敗，但輪詢仍連得上 → 不可誤判為就緒
    if port_open "$port"; then
        echo "錯誤：${name} 的 port ${port} 已被其他程式占用（不是 var/run/${name}.pid 記錄的行程），先關閉占用程式。" >&2
        exit 1
    fi

    # 先 setsid 讓服務自成 session / process group（pgid == 記錄的 pid），dev_down 才能對整個
    # 行程樹送訊號（just → uv → uvicorn 這類包裝會讓真正監聽 port 的是孫行程）；
    # 再 exec 讓記錄的 pid 就是服務本身（不是包一層的 sh），之後才能以 ps 比對命令列
    (
        cd "$ROOT"
        nohup python3 -c 'import os, sys; os.setsid(); os.execvp("sh", ["sh", "-c", "exec " + sys.argv[1]])' \
            "$cmd" > "$log" 2>&1 < /dev/null &
        echo $! > "$pid_file"
    )
    printf '%s' "$cmd" > "$cmd_file"
    pid="$(cat "$pid_file")"
    wait_ready "$name" "$pid" "$port"
}

echo "==> [1/3] 本機 DB / SeaweedFS"
if [ "${DEV_UP_SKIP_DB:-}" = "1" ]; then
    echo "    DEV_UP_SKIP_DB=1，略過"
else
    (cd "$ROOT" && just db-start)
fi

echo "==> [2/3] API"
start_service api "$API_CMD" "$API_PORT"

echo "==> [3/3] Web"
start_service web "$WEB_CMD" "$WEB_PORT"

cat <<'EOF'

全部就緒：
  後台    http://127.0.0.1:5341/
  家長端  http://127.0.0.1:5341/parent/
  API     http://127.0.0.1:8341/docs
log 在 var/log/，pid 在 var/run/。
EOF
