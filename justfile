# afterschool — 所有本機操作的唯一入口（INFRA-002）
#
# 慣例：
# - 各 app 自己讀自己的 .env，root 不載入（dotenv-load := false）。
# - 所有 recipe 用 justfile_directory() 組絕對路徑，不依賴呼叫端 cwd。
# - 本專案禁止全量 lint / typecheck / test：標 ★ 的 recipe 無參數時印用法並 exit 1。
#   唯一例外是 web-typecheck（vue-tsc 只能全專案跑），只在送 PR 前執行一次。
# - Python 類 recipe 的路徑路由見 scripts/_pyroute.sh（只支援 apps/api/ 與 scripts/ 底下）。
# - supabase / pnpm / uv / docker 一律從 PATH 找執行檔（回歸測試會用假執行檔替換）。

set dotenv-load := false
set positional-arguments := true

root := justfile_directory()

# 列出所有可用的 recipe
default:
    @just --justfile "{{ root }}/justfile" --list

# ---------------------------------------------------------------------------
# 後端（Python）測試 / lint / typecheck：路徑必填
# ---------------------------------------------------------------------------

# ★ 後端單元測試：just test PATH... [pytest 參數...]（自動排除 integration）
test *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{ root }}"
    source "$ROOT/scripts/_pyroute.sh"
    py_prepare "用法: just test PATH... [pytest 參數...]" "$@"
    # 使用者另帶 -m 時排在後面，pytest 以最後一個 -m 為準。
    py_exec pytest "${PY_PATHS[@]}" -m "not integration" ${PY_EXTRA[@]+"${PY_EXTRA[@]}"}

# ★ 後端整合測試：just test-int PATH... [pytest 參數...]（需本機 Supabase）
test-int *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{ root }}"
    source "$ROOT/scripts/_pyroute.sh"
    py_prepare "用法: just test-int PATH... [pytest 參數...]" "$@"
    # JUST_TEST_INT_DB_PORT 只供 justfile 回歸測試指向未監聽的 port，平常不要設定。
    db_port="${JUST_TEST_INT_DB_PORT:-54342}"
    if ! python3 -c 'import socket, sys; socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1).close()' "$db_port" 2>/dev/null; then
        echo "錯誤：本機 Supabase 未啟動（127.0.0.1:${db_port} 連不上），先跑 just db-start" >&2
        exit 1
    fi
    py_exec pytest "${PY_PATHS[@]}" -m integration ${PY_EXTRA[@]+"${PY_EXTRA[@]}"}

# ★ ruff check + ruff format --check：just lint PATH...
lint *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{ root }}"
    source "$ROOT/scripts/_pyroute.sh"
    py_prepare "用法: just lint PATH..." "$@"
    status=0
    py_exec ruff check ${PY_RUFF_CONFIG[@]+"${PY_RUFF_CONFIG[@]}"} "${PY_PATHS[@]}" ${PY_EXTRA[@]+"${PY_EXTRA[@]}"} || status=$?
    py_exec ruff format --check ${PY_RUFF_CONFIG[@]+"${PY_RUFF_CONFIG[@]}"} "${PY_PATHS[@]}" || status=$?
    exit "$status"

# ★ mypy 型別檢查：just typecheck PATH...
typecheck *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{ root }}"
    source "$ROOT/scripts/_pyroute.sh"
    py_prepare "用法: just typecheck PATH..." "$@"
    py_exec mypy ${PY_MYPY_CONFIG[@]+"${PY_MYPY_CONFIG[@]}"} "${PY_PATHS[@]}" ${PY_EXTRA[@]+"${PY_EXTRA[@]}"}

# ---------------------------------------------------------------------------
# 前端測試 / lint / typecheck：檔案必填（路徑相對 apps/web）
# ---------------------------------------------------------------------------

# ★ 前端 vitest：just web-test FILE [vitest 參數...]（FILE 相對 apps/web）
web-test *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ $# -eq 0 ] || [ "${1#-}" != "$1" ]; then
        echo "用法: just web-test FILE [vitest 參數...]（FILE 相對 apps/web，例如 src/x.spec.ts）" >&2
        exit 1
    fi
    case "${1%/}" in
        . | src | ./src)
            echo "錯誤：$1 等同全量執行。禁止全量執行，請指定檔案或更小的子目錄。" >&2
            exit 1
            ;;
    esac
    cd "{{ root }}/apps/web"
    pnpm exec vitest run "$@"

# ★ 前端 eslint：just web-lint FILE...（FILE 相對 apps/web）
web-lint *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ $# -eq 0 ]; then
        echo "用法: just web-lint FILE...（FILE 相對 apps/web，例如 src/main.ts）" >&2
        exit 1
    fi
    for f in "$@"; do
        case "${f%/}" in
            . | src | ./src)
                echo "錯誤：$f 等同全量執行。禁止全量執行，請指定檔案或更小的子目錄。" >&2
                exit 1
                ;;
        esac
    done
    cd "{{ root }}/apps/web"
    pnpm exec eslint "$@"

# 前端 vue-tsc 全專案型別檢查（唯一允許無參數的檢查類 recipe，只在送 PR 前跑一次）
web-typecheck:
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{ root }}/apps/web"
    pnpm exec vue-tsc --noEmit -p tsconfig.app.json

# ---------------------------------------------------------------------------
# 本機 Supabase
# ---------------------------------------------------------------------------

# 啟動本機 Supabase（API 54341 / DB 54342 / Studio 54343）
db-start:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! docker info >/dev/null 2>&1; then
        echo "錯誤：Docker 未啟動，先啟動 Docker Desktop（或相容的 daemon）。" >&2
        exit 1
    fi
    cd "{{ root }}"
    supabase start

# 停止本機 Supabase（保留 volume，資料下次啟動還在）
db-stop:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! docker info >/dev/null 2>&1; then
        echo "錯誤：Docker 未啟動，先啟動 Docker Desktop（或相容的 daemon）。" >&2
        exit 1
    fi
    cd "{{ root }}"
    supabase stop

# 重建本機 DB（套用全部 migration + seed）：just db-reset [--yes]，只會動本機
db-reset *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    # 參數白名單只認 --yes：--linked / --db-url 之類會讓 reset 打到遠端專案，一律不轉發。
    assume_yes=0
    for arg in "$@"; do
        case "$arg" in
            --yes) assume_yes=1 ;;
            *)
                echo "錯誤：just db-reset 只接受 --yes，拒絕 ${arg}（只重置本機，不提供任何操作遠端的路徑）。" >&2
                exit 1
                ;;
        esac
    done
    if [ "$assume_yes" != "1" ]; then
        if [ ! -t 0 ]; then
            echo "錯誤：非互動環境必須明確帶 --yes 才會執行：just db-reset --yes" >&2
            exit 1
        fi
        read -r -p "這會清空本機 Supabase 的所有資料並重跑 migration + seed，輸入 yes 繼續：" reply
        if [ "$reply" != "yes" ]; then
            echo "已取消，資料未被更動。" >&2
            exit 1
        fi
    fi
    cd "{{ root }}"
    supabase db reset
    # migration + seed 套用成功後立刻驗 RLS（還沒有任何表時允許為空）；違規時 db-reset 以非 0 結束
    just --justfile "{{ root }}/justfile" check-rls --allow-empty

# RLS / 權限檢查（INFRA-007）：just check-rls [--db-url URL] [--schema public] [--allow-empty] [--allow-remote]
check-rls *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -x "{{ root }}/apps/api/.venv/bin/python" ]; then
        echo "錯誤：找不到 apps/api/.venv，先在 apps/api 執行 uv sync --frozen（或 just bootstrap）。" >&2
        exit 2
    fi
    "{{ root }}/apps/api/.venv/bin/python" "{{ root }}/scripts/check_rls.py" "$@"

# ★ 新增一支 migration：just db-new-migration NAME（snake_case，timestamp 由 CLI 自動帶）
db-new-migration *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ $# -ne 1 ]; then
        echo "用法: just db-new-migration NAME（snake_case，例如 create_students）" >&2
        exit 1
    fi
    if ! [[ "$1" =~ ^[a-z][a-z0-9_]*$ ]]; then
        echo '錯誤：migration 名稱必須符合 ^[a-z][a-z0-9_]*$（snake_case），收到：'"${1}" >&2
        exit 1
    fi
    cd "{{ root }}"
    supabase migration new "$1"

# ---------------------------------------------------------------------------
# 開發伺服器與工具
# ---------------------------------------------------------------------------

# 啟動 FastAPI（http://127.0.0.1:8341，--reload）
api:
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{ root }}/apps/api"
    uv run --frozen uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8341

# 啟動 Vite dev server（http://127.0.0.1:5341）
web:
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{ root }}/apps/web"
    pnpm exec vite --host 127.0.0.1 --port 5341 --strictPort

# 驗證 docs/tasks 的 tasks.json（參數原樣轉給 scripts/validate_tasks.py，例如 --ready INFRA）
validate-tasks *ARGS:
    python3 "{{ root }}/scripts/validate_tasks.py" "{{ root }}" "$@"
