# shellcheck shell=bash
# justfile 的 Python 類 recipe（test / test-int / lint / typecheck）共用的路徑路由（INFRA-002）。
#
# 用法（在 recipe 內）：
#   ROOT="{{ root }}"; source "$ROOT/scripts/_pyroute.sh"
#   py_prepare "用法: just test PATH... [pytest 參數...]" "$@"
#   py_exec pytest "${PY_PATHS[@]}" ${PY_EXTRA[@]+"${PY_EXTRA[@]}"}
#
# 路由規則：
# - apps/api/ 底下：cwd 切到 apps/api，路徑轉成相對 apps/api，用 `uv run --frozen --project` 執行。
# - scripts/ 底下：cwd 留在 repo root，用 apps/api/.venv/bin/<tool> 執行；
#   ruff / mypy 另帶 apps/api/pyproject.toml 設定（PY_RUFF_CONFIG / PY_MYPY_CONFIG）。
# - 其他前綴一律拒絕；等同全量的路徑（apps/api、apps/api/tests、scripts 等）一律拒絕。
#
# macOS 內建 bash 3.2：空陣列展開一律寫成 ${arr[@]+"${arr[@]}"}。

PY_PATHS=()
PY_EXTRA=()
PY_ROUTE=""
PY_RUFF_CONFIG=()
PY_MYPY_CONFIG=()

# 去掉開頭的 ./ 與 repo root 絕對路徑前綴，印出 repo 相對路徑。
py_normalize_path() {
    local p="$1"
    case "$p" in
        "$ROOT"/*) p="${p#"$ROOT"/}" ;;
    esac
    while [ "${p#./}" != "$p" ]; do
        p="${p#./}"
    done
    printf '%s\n' "$p"
}

# 路徑（去掉 pytest node id 的 ::xxx 與結尾斜線）等同全量時回傳 0。
py_is_full_run() {
    local p="${1%%::*}"
    while [ "${p%/}" != "$p" ]; do
        p="${p%/}"
    done
    case "$p" in
        "" | . | apps | apps/api | apps/api/tests | apps/api/tests/unit | apps/api/tests/integration | scripts | scripts/tests)
            return 0
            ;;
    esac
    return 1
}

# 解析參數：開頭連續的非選項參數是路徑，第一個 - 開頭的參數之後全部原樣轉給工具。
# 驗證路徑、決定路由、切換 cwd。失敗時印訊息到 stderr 並 exit 1。
py_prepare() {
    local usage="$1"
    shift
    local raw=()
    while [ $# -gt 0 ]; do
        case "$1" in
            -*) break ;;
        esac
        raw+=("$1")
        shift
    done
    PY_EXTRA=("$@")

    if [ ${#raw[@]} -eq 0 ]; then
        echo "$usage" >&2
        echo "至少指定一個檔案或子目錄；本專案禁止無參數的全量執行。" >&2
        exit 1
    fi

    local p route
    for p in "${raw[@]}"; do
        p="$(py_normalize_path "$p")"
        if py_is_full_run "$p"; then
            echo "錯誤：${p} 等同全量執行。禁止全量執行，請指定檔案或更小的子目錄。" >&2
            exit 1
        fi
        case "$p" in
            apps/api/*) route="api" ;;
            scripts/*) route="scripts" ;;
            *)
                echo "錯誤：${p} 不在支援的範圍。只支援 apps/api/ 與 scripts/ 底下的路徑。" >&2
                exit 1
                ;;
        esac
        if [ -n "$PY_ROUTE" ] && [ "$PY_ROUTE" != "$route" ]; then
            echo "錯誤：apps/api/ 與 scripts/ 的路徑請分開執行（兩者的 cwd 與設定不同）。" >&2
            exit 1
        fi
        PY_ROUTE="$route"
        if [ "$route" = "api" ]; then
            PY_PATHS+=("${p#apps/api/}")
        else
            PY_PATHS+=("$p")
        fi
    done

    if [ "$PY_ROUTE" = "api" ]; then
        cd "$ROOT/apps/api"
    else
        cd "$ROOT"
        PY_RUFF_CONFIG=(--config "$ROOT/apps/api/pyproject.toml")
        PY_MYPY_CONFIG=(--config-file "$ROOT/apps/api/pyproject.toml")
        if [ ! -x "$ROOT/apps/api/.venv/bin/python" ]; then
            echo "錯誤：找不到 apps/api/.venv，先在 apps/api 執行 uv sync --frozen（或 just bootstrap）。" >&2
            exit 1
        fi
    fi
}

# 依路由執行工具：py_exec <tool> [args...]
py_exec() {
    local tool="$1"
    shift
    if [ "$PY_ROUTE" = "api" ]; then
        uv run --frozen --project "$ROOT/apps/api" "$tool" "$@"
    else
        "$ROOT/apps/api/.venv/bin/$tool" "$@"
    fi
}
