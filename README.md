# afterschool

國小安親班管理系統，單一安親班使用（單一租戶）。包含員工使用的管理後台與家長使用的 LINE LIFF 家長端，涵蓋帳號角色權限、學生 / 班級 / 家長、出勤、請假、作業進度、接送管理與考試成績。後端為 FastAPI + Supabase（PostgreSQL），前端為 Vue 3（後台 Element Plus、家長端自製 M3 元件），部署在 Railway。

## 必要工具

| 工具 | 版本 | 用途 |
|---|---|---|
| just | 最新版 | 所有本機操作的唯一入口（root `justfile`） |
| uv | 最新版 | 後端套件管理，Python 由 uv 管理 |
| Python | 3.13（由 uv 安裝與管理） | 後端 `apps/api` |
| Node | 24 | 前端 `apps/web` |
| pnpm | 11.x | 前端套件管理 |
| Supabase CLI | 最新版 | 本機 Supabase 與 migration |
| Docker | Docker Desktop 或相容 daemon | 本機 Supabase 容器 |

工具是否齊全、版本是否正確、port 是否被占用，用 `just doctor` 檢查。

## 快速開始

```bash
just bootstrap        # 一次性初始化：安裝後端與前端依賴、建立 .env
just db-start         # 啟動本機 Supabase（需要 Docker）
just db-reset --yes   # 重建本機 DB：套用全部 migration + seed
just api              # 啟動 FastAPI（http://127.0.0.1:8341）
just web              # 啟動 Vite dev server（http://127.0.0.1:5341）
```

`just api` 與 `just web` 也可以改用 `just up` 一次啟動全套（Supabase + API + Web）。

端對端測試（Playwright，預設不執行、只打本機）：首次先 `cd apps/web && pnpm exec playwright install chromium` 安裝瀏覽器，服務啟動後以 `just e2e e2e/smoke.spec.ts` 指定檔案執行；`E2E_BASE_URL` 不是本機時設定載入即失敗。

## 本機 port

| 服務 | port |
|---|---|
| Supabase API | 54341 |
| Supabase DB（PostgreSQL） | 54342 |
| Supabase Studio | 54343 |
| Supabase 其他服務（shadow DB 54340、Inbucket 54344~54346、Analytics 54347、Edge Runtime inspector 54348、Pooler 54349） | 54340、54344~54349 |
| FastAPI | 8341 |
| Vite dev server | 5341 |

所有 port 都避開 5432x / 5433x（同機其他專案在用）。設定來源是 `supabase/config.toml`。

## 禁止全量 lint / typecheck / test

任何 lint、format、typecheck、test 指令都必須指定路徑、檔案或 marker，`justfile` 的對應 recipe 無參數時會直接拒絕執行：

```bash
just test apps/api/tests/unit/services/test_leave_service.py -k apply
just test-int apps/api/tests/integration/test_rls.py
just lint apps/api/app/services/leave_service.py
just web-test src/components/pickup/QueueCard.spec.ts
just web-lint src/components/pickup/QueueCard.vue
```

禁止無參數的 `pytest`、`pytest apps/api/tests/`、`ruff check .`、`pnpm lint`、無檔案的 `pnpm vitest run`。唯一例外是 `just web-typecheck`（vue-tsc 只能全專案跑），只在送 PR 前執行一次。全量執行只在 CI。

## 文件索引

| 文件 | 內容 |
|---|---|
| `CLAUDE.md` | 開發規範與工作規則 |
| `docs/architecture_decisions.md` | 架構決策 |
| `docs/domain_spec.md` | 功能、資料模型、API、權限碼、通知事件、頁面 |
| `docs/testing_conventions.md` | 測試慣例 |
| `docs/tasks/README.md` | 實作規格（tasks.json）的使用規則 |
| `docs/deployment.md` | Railway + Supabase 雲端部署流程 |
