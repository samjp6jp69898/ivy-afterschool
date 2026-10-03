# afterschool 後端正式部署映像（INFRA-031）。Railway 服務的 Root Directory 為 apps/api，build context 即本目錄。
#
# - 依賴層與程式碼層分離：只有 uv.lock / pyproject.toml 變動才重跑依賴安裝。
# - 容器啟動不跑 migration：Railway pre-deploy command 以同一映像執行 `python -m app.cli migrate`，
#   因此映像必須包含 alembic.ini 與 alembic/（含 versions/）。
# - --workers 1 是硬性要求：WebSocket broadcaster 與 APScheduler 都在行程內（architecture_decisions §7）。

FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.3 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN uv sync --frozen --no-dev


FROM python:3.13-slim AS runtime

# tini：轉發 SIGTERM、回收殭屍行程
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app /app/app
COPY --from=builder /app/alembic.ini /app/alembic.ini
COPY --from=builder /app/alembic /app/alembic

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# 只信任 Railway private network（web 服務的 nginx 經此轉送）；不得改成 *。
# 實際範圍上線時實測（INFRA-031 open_design_questions），uvicorn 原生讀取此變數。
ENV FORWARDED_ALLOW_IPS=fd12::/16

# 非 root 執行；/app 維持唯讀（上傳一律走 R2）
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER app

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--"]
# --host :: 同時監聽 IPv4 / IPv6（Railway private network 走 IPv6）
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host :: --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS}\" --no-access-log"]
