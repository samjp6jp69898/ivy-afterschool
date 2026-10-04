#!/usr/bin/env bash
# 安全停止 dev_up.sh 啟動的本機服務（INFRA-017）：just down [--all]
#
# 對 var/run/{api,web}.pid：行程存活且 ps 命令列與 .cmd 相符才送 SIGTERM（最多等 10 秒，仍存活
# 則 SIGKILL）；訊號送給整個 process group（dev_up.sh 讓服務自成 group，pgid == pid，
# 才涵蓋 just → uv → uvicorn 這類包裝下真正監聽 port 的子行程）。pgid != pid 時（不是 dev_up 建立的）
# 只對該 pid 送訊號。pid 被系統重用（命令列不符）時不送任何訊號，只印警告並清掉殘留檔案。
# 預設不動 DB；--all 才額外執行 just db-stop（compose stop，保留 volume）。
#
# 測試用覆寫：DEV_UP_ROOT（與 dev_up.sh 共用）。macOS 內建 bash 3.2 相容。

set -euo pipefail

if [ -n "${DEV_UP_ROOT:-}" ]; then
    ROOT="$DEV_UP_ROOT"
else
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
RUN_DIR="$ROOT/var/run"
STOP_DB=0

case "$#:${1:-}" in
    0:) ;;
    1:--all) STOP_DB=1 ;;
    *)
        echo "用法: just down [--all]（--all 會一併停止本機 DB / SeaweedFS）" >&2
        exit 1
        ;;
esac

is_alive() {
    kill -0 "$1" 2>/dev/null
}

# 與 dev_up.sh 的 command_matches 相同規則：ps 命令列整串等於記錄指令，或僅差一個直譯器前綴。
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

# 目標還有行程存活：group 模式看整個 process group，否則只看 pid
target_alive() {
    if [ "$1" = "group" ]; then
        kill -0 -- "-$2" 2>/dev/null
    else
        is_alive "$2"
    fi
}

send_signal() {
    if [ "$1" = "group" ]; then
        kill "-$3" -- "-$2" 2>/dev/null || true
    else
        kill "-$3" "$2" 2>/dev/null || true
    fi
}

stop_service() {
    local name="$1" pid_file="$RUN_DIR/$1.pid" cmd_file="$RUN_DIR/$1.cmd" pid="" recorded="" waited=0
    local pgid="" mode="pid"

    if [ -f "$pid_file" ]; then
        pid="$(tr -dc '0-9' < "$pid_file")"
    fi
    if [ -z "$pid" ] || ! is_alive "$pid"; then
        rm -f "$pid_file" "$cmd_file"
        echo "    ${name} 未在執行"
        return 0
    fi

    [ -f "$cmd_file" ] && recorded="$(cat "$cmd_file")"
    if [ -z "$recorded" ] || ! command_matches "$pid" "$recorded"; then
        rm -f "$pid_file" "$cmd_file"
        echo "    警告：pid ${pid} 已不是 ${name}，略過" >&2
        return 0
    fi

    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -dc '0-9')"
    [ "$pgid" = "$pid" ] && mode="group"

    send_signal "$mode" "$pid" TERM
    while target_alive "$mode" "$pid" && [ "$waited" -lt 10 ]; do
        sleep 1
        waited=$((waited + 1))
    done
    if target_alive "$mode" "$pid"; then
        send_signal "$mode" "$pid" KILL
        echo "    ${name} 未在 10 秒內結束，已強制終止（pid ${pid}）"
    else
        echo "    ${name} 已停止（pid ${pid}）"
    fi
    rm -f "$pid_file" "$cmd_file"
}

echo "==> 停止 API / Web"
stop_service api
stop_service web

if [ "$STOP_DB" = "1" ]; then
    echo "==> 停止本機 DB / SeaweedFS"
    (cd "$ROOT" && just db-stop)
fi
