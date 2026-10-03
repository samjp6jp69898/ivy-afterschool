#!/usr/bin/env bash
# 一次性、可重複執行的本機環境初始化（INFRA-015）：just bootstrap
#
# 步驟：前置工具檢查 → uv sync（apps/api）→ pnpm install（apps/web）→ apps/api/.env
#       → git hooks（justfile 有 install-hooks 時）→ 下一步提示。
# 冪等：apps/api/.env 已存在時完全不動；套件安裝以 lockfile 為準，重跑無副作用。
# 刻意不做：不啟動本機 DB（要 Docker、耗時），由使用者以 just db-start 明確觸發。
#
# BOOTSTRAP_ROOT 可覆寫 repo root（測試用）。macOS 內建 bash 3.2 相容。

set -euo pipefail

if [ -n "${BOOTSTRAP_ROOT:-}" ]; then
    ROOT="$BOOTSTRAP_ROOT"
else
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

step() { printf '==> %s\n' "$1"; }

# --- 1. 前置工具 -------------------------------------------------------------
for tool in uv pnpm; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "找不到 ${tool}，請先安裝（見 README）" >&2
        exit 1
    fi
done

# --- 2. 後端依賴 -------------------------------------------------------------
step "apps/api：uv sync --frozen"
(cd "$ROOT/apps/api" && uv sync --frozen)

# --- 3. 前端依賴 -------------------------------------------------------------
step "apps/web：pnpm install --frozen-lockfile"
(cd "$ROOT/apps/web" && pnpm install --frozen-lockfile)

# --- 4. apps/api/.env --------------------------------------------------------
env_file="$ROOT/apps/api/.env"
if [ -e "$env_file" ]; then
    step "apps/api/.env 已存在，略過"
else
    step "apps/api/.env：由 .env.example 建立並產生 APP_SECRET_KEY"
    secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
    tmp="$(mktemp "$ROOT/apps/api/.env.XXXXXX")"
    trap 'rm -f "$tmp"' EXIT
    chmod 600 "$tmp"
    # token_urlsafe 只含 [A-Za-z0-9_-]，可安全放進 sed 的替換字串
    sed "s|^APP_SECRET_KEY=change-me\$|APP_SECRET_KEY=${secret}|" "$ROOT/apps/api/.env.example" > "$tmp"
    mv "$tmp" "$env_file"
    trap - EXIT
fi

# --- 5. git hooks ------------------------------------------------------------
if [ -f "$ROOT/justfile" ] \
    && just --justfile "$ROOT/justfile" --summary 2>/dev/null | tr ' ' '\n' | grep -qx install-hooks; then
    step "git hooks：just install-hooks"
    just --justfile "$ROOT/justfile" install-hooks
else
    step "justfile 沒有 install-hooks，略過 git hooks"
fi

# --- 6. 下一步 ---------------------------------------------------------------
cat <<'EOF'

完成。下一步：
  1. just db-start         啟動本機 Postgres 與 SeaweedFS（需要 Docker）
  2. just db-reset --yes   建立本機 DB（套用全部 Alembic revision、設定 app_backend 本機密碼）
  3. just up               啟動 API 與 Web
EOF
