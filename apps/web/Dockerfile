# afterschool web 服務部署映像（INFRA-034）：pnpm 建置 SPA，nginx 提供靜態檔並反代 /api（INFRA-033）。
# Railway 服務的 Root Directory 為 apps/web，build context 即本目錄。前端沒有 build-time env（INFRA-006）。

FROM node:24-slim AS build

WORKDIR /app
RUN corepack enable

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm exec vite build


FROM nginx:1.27-alpine AS runtime

COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY nginx/security-headers.conf /etc/nginx/security-headers.conf
COPY --chmod=755 nginx/40-real-ip.sh /docker-entrypoint.d/40-real-ip.sh
COPY --from=build /app/dist /usr/share/nginx/html
# .vite/ 內的模組圖（parent-module-graph.json）列出所有原始碼與依賴路徑，不對外提供
RUN find /usr/share/nginx/html -name '*.map' -delete \
    && rm -rf /usr/share/nginx/html/.vite

# TRUSTED_EDGE_CIDRS：Railway edge 的來源範圍，上線實測前的保守預設（INFRA-033 open_design_questions）。
# 100.64.0.0/10 為 CGNAT 保留位址，網際網路上的用戶端無法以此為來源，設錯只會讓 audit 記到 edge IP，
# 不會讓用戶端偽造 client IP；不得放寬成 0.0.0.0/0。
# BACKEND_URL 刻意不給預設：缺值時 envsubst 留下空字串，nginx 啟動失敗（fail-fast）。
ENV PORT=8080 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=true \
    TRUSTED_EDGE_CIDRS=100.64.0.0/10

EXPOSE 8080
