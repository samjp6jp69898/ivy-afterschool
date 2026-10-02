# 架構決策

> 只記錄目前有效的最終決策，異動歷史交給 git log。功能與資料模型見 `docs/domain_spec.md`。

## 1. 產品定位

國小安親班管理系統，單一安親班使用（**單一租戶，不做多租戶**）。功能模組從 ivy（幼稚園管理系統，`/Users/user/personal_project/ivy/ivyManageSystem-{backend,frontend}`）移植改寫，task 的 `source_ref` 欄位標示對應的 ivy 來源檔案/方法。

使用者端只有兩個：

| 端 | 使用者 | 形式 |
|---|---|---|
| 管理後台 | 安親班所有員工（管理員、主任、行政、課輔老師） | Vue 3 + Element Plus SPA，桌機/平板 |
| 家長端 | 家長 | LINE LIFF + PWA（沿用 ivy 家長端做法，自製 M3 元件） |

**沒有教師端**。ivy 中只能從教師端（`/api/portal/*`、`views/portal/*`）完成的動作（點名、接送確認、聯絡事項），一律改由管理後台以權限碼控管。

## 2. 技術棧

| 層 | 選擇 | 備註 |
|---|---|---|
| 後端 | Python 3.13 + FastAPI + SQLAlchemy 2（sync，psycopg 3）+ Pydantic v2 | 與 ivy 一致，方便移植 service 層 |
| DB | Supabase（PostgreSQL 15+） | 本機用 Supabase CLI（`supabase start`），雲端用 Supabase 專案 |
| Migration | Supabase CLI SQL migration（`supabase/migrations/*.sql`） | **不用 Alembic**。SQLAlchemy model 必須與 migration 一致，由 schema drift 測試把關 |
| 認證 | 自建 JWT（HS256）放 httpOnly cookie + refresh token family | 移植 ivy `utils/auth.py`、`utils/cookie.py`；**不使用 Supabase Auth** |
| 家長登入 | LINE LIFF id_token → 後端驗證 → 綁定碼綁定學生 | 移植 ivy `services/line_login_service.py`、`models/parent_binding.py` |
| 即時推送 | FastAPI WebSocket + 本機 broadcaster | 接送佇列、作業進度看板。Railway 單一實例；多實例時才加 Redis（見 §7） |
| 通知 | 中央分派器 + outbox（in_app / line / ws） | 移植 ivy `services/notification/` |
| 前端 | Vue 3.5 + TypeScript strict + Vite + Pinia + Vue Router（hash）+ axios | 單一 Vite 專案、兩個 HTML 入口：`index.html`（後台）、`parent/index.html`（家長端） |
| UI | 後台 Element Plus；家長端自製 M3 元件 | 移植 ivy `src/parent/components/m3/` |
| 測試 | pytest（unit / integration）、Vitest + @vue/test-utils、Playwright（e2e，少量關鍵流程） | 見 `docs/testing_conventions.md` |
| 套件管理 | 後端 uv（`apps/api/pyproject.toml` + `uv.lock`）；前端 pnpm | |
| 指令入口 | root `justfile` | 所有 lint / test 指令都帶路徑參數 |
| 部署 | Railway（api service + web 靜態 service）+ Supabase 雲端 | 同 ivy 現行做法 |

## 3. Repo 結構

```
afterschool/
├── apps/
│   ├── api/                 # FastAPI 後端
│   │   ├── app/
│   │   │   ├── main.py      # create_app()
│   │   │   ├── core/        # config（env Settings）、db、security、errors、logging
│   │   │   ├── models/      # SQLAlchemy models（對應 supabase/migrations）
│   │   │   ├── schemas/     # Pydantic request/response
│   │   │   ├── repositories/# 資料存取（純 DB 操作）
│   │   │   ├── services/    # 業務邏輯（一個方法一個 task）
│   │   │   ├── api/
│   │   │   │   ├── admin/   # 後台 API（/api/admin/*）
│   │   │   │   ├── parent/  # 家長端 API（/api/parent/*）
│   │   │   │   ├── device/  # 打卡機 API（/api/device/*，NFC，目前 blocked）
│   │   │   │   └── ws/      # WebSocket
│   │   │   └── notifications/ # 分派器、頻道、事件定義
│   │   └── tests/           # unit/ 與 integration/
│   └── web/                 # Vue 3 前端（後台 + 家長端）
│       ├── index.html
│       ├── parent/index.html
│       └── src/
│           ├── api/ components/ composables/ layouts/ router/ stores/ views/ utils/ constants/   # 後台
│           ├── shared/      # 後台與家長端共用（型別、日期工具、http 基底）
│           └── parent/      # 家長端（api/ components/ views/ stores/ router.ts）
├── supabase/                # config.toml、migrations/、seed.sql
├── docs/                    # 規格、tasks、mockups
├── scripts/                 # validate_tasks.py、開發腳本
└── justfile
```

## 4. 設定值分層（env 只放基礎設施與 secret）

設定值分三層，**業務設定一律進 DB、由後台調整**，不寫在 env：

| 層 | 放什麼 | 例子 |
|---|---|---|
| env（`apps/api/.env`，Pydantic Settings） | 只放「沒有它程式起不來」或 secret 的基礎設施值 | `DATABASE_URL`、`APP_SECRET_KEY`（JWT 簽章 + DB 內 secret 加密金鑰）、`APP_ENV`、`CORS_ORIGINS`、`PUBLIC_BASE_URL`、`SENTRY_DSN` |
| DB `system_settings`（後台「系統設定」頁可改） | 營運參數，key/value + 每個 key 有 Pydantic schema 驗證 | 安親班名稱/Logo、營業時段、接送時段、作業進度預設預計完成時間、通知文案開關、LIFF ID、LINE Messaging channel token/secret（加密存放） |
| DB 參考資料表（後台各自的管理頁） | 可增刪的清單型設定 | 科目、考試類型、合作國小清單、作業項目範本、休假日 |

- 系統設定讀取走有 TTL 的 in-process cache，後台修改後立即失效該 key。
- DB 內的 secret（LINE channel token 等）以 `APP_SECRET_KEY` 衍生的金鑰做對稱加密存放，API 回傳時只回遮罩值。
- 新增設定項時：先在 `app/core/settings_registry.py` 註冊 key + schema + 預設值，再由 migration seed 預設值；**不要**為業務參數新增 env 變數。

## 5. 資料庫原則

- 所有 table 在 `public` schema，主鍵 `uuid default gen_random_uuid()`，時間欄位 `timestamptz`，含 `created_at` / `updated_at`（trigger 維護）。
- **RLS 一律開啟且不給 `anon` / `authenticated` 任何 policy**：資料只透過 FastAPI 存取，後端以專用 DB role 連線。這讓 Supabase 的 PostgREST / Realtime 對外不暴露任何資料，屬縱深防禦。由 `scripts/check_rls.py` + integration 測試把關（每張表都 `rowsecurity = true` 且 `anon` 查詢回 0 筆/拒絕）。
- 軟刪除只用在有歷史意義的主檔（students、guardians、classes）：`archived_at`；交易紀錄不刪除。
- 敏感個資（學生身分證字號、健康備註）用 pgcrypto 或應用層加密欄位，移植 ivy Student 醫療欄位加密做法。
- 日期一律以 `Asia/Taipei` 判斷「今天」（出勤日、接送日、作業日），由 `app/core/clock.py` 統一提供，測試可注入。

## 6. 權限模型（移植 ivy RBAC）

- 扁平權限碼字串（例如 `students:read`、`students:write`、`pickup:operate`、`exams:publish`），定義在 `app/core/permissions.py` 的 `Permission` enum，前端同步一份 `src/constants/permissions.ts`（有一致性測試）。
- `roles` 表存角色與權限碼陣列；預設角色由 seed 建立：`admin`（全部，不可刪）、`director`（主任）、`clerk`（行政）、`tutor`（課輔老師）。**家長不是 roles 表中的角色**，家長帳號是獨立的 `parent_accounts`，走 `/api/parent/*` 與獨立 guard。
- 員工帳號可在角色之外個別加/減權限碼（`staff_users.extra_permissions` / `revoked_permissions`）。
- 後端守衛：`require_permission(Permission.X)`；家長守衛：`require_parent()` + `assert_parent_owns_student()`（IDOR 防護，移植 ivy `_assert_student_owned`）。
- 前端：路由 `meta.permission` + `authGuard` 預設拒絕；頁內用 `hasPermission()`。

## 7. 即時推送與擴展

- Railway 先以單一實例運行，WebSocket 廣播用 in-process broadcaster（介面與 ivy `utils/broadcast` 相同，保留 Redis 實作的接縫但不實作）。
- 背景工作（通知 outbox 重送、每日出勤初始化）用 FastAPI lifespan 內的輕量排程（APScheduler）+ DB 鎖確保冪等。

## 8. NFC 打卡

NFC 機器尚未到貨，**整段 task 標 `blocked`**：裝置註冊、卡號綁定、打卡 API、打卡機畫面。資料模型中先保留 `nfc_cards`、`devices` 的位置（同樣是 blocked 的 DB task），機型 / 通訊協定 / 離線行為確定後再細拆。人工出勤登記（後台）不受影響，照常實作。

## 9. 不做的範圍

教師端、多租戶、校車/娃娃車、學費帳務、成長冊/作品集、用藥、政府報表、HR（薪資/排班/員工出勤）、公告系統、大螢幕叫號（列為 open question）。
