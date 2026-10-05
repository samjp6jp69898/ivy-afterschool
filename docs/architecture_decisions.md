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
| DB | PostgreSQL 17（雲端 Railway Postgres） | 本機用 repo 內 `compose.yaml` 起同主版本的 Postgres（`127.0.0.1:54342`）；不使用任何 DB 廠商的 Auth / Storage / REST 附加服務 |
| Migration | Alembic（`apps/api/alembic/`） | revision 手寫，`upgrade()` 以 `op.execute` 寫原生 SQL；forward-only。SQLAlchemy model 必須與 migration 一致，由 schema drift 檢查（Alembic autogenerate 比對）把關。見 §5 |
| 檔案儲存 | Cloudflare R2（S3 相容 API，boto3） | 單一私有 bucket，後端上傳並簽發短效 presigned URL；本機用 `compose.yaml` 的 SeaweedFS 模擬（S3 API）。見 §10 |
| 認證 | 自建 JWT（HS256）放 httpOnly cookie + refresh token family | 移植 ivy `utils/auth.py`、`utils/cookie.py` |
| 家長登入 | LINE LIFF id_token → 後端驗證 → 綁定碼綁定學生 | 移植 ivy `services/line_login_service.py`、`models/parent_binding.py` |
| 即時推送 | FastAPI WebSocket + 本機 broadcaster | 接送佇列、作業進度看板。Railway 單一實例；多實例時才加 Redis（見 §7） |
| 通知 | 中央分派器 + outbox（in_app / line / ws） | 移植 ivy `services/notification/` |
| 前端 | Vue 3.5 + TypeScript strict + Vite + Pinia + Vue Router（hash）+ axios | 單一 Vite 專案、兩個 HTML 入口：`index.html`（後台）、`parent/index.html`（家長端） |
| UI | 後台 Element Plus；家長端自製 M3 元件 | 移植 ivy `src/parent/components/m3/` |
| 測試 | pytest（unit / integration）、Vitest + @vue/test-utils、Playwright（e2e，少量關鍵流程） | 見 `docs/testing_conventions.md` |
| 套件管理 | 後端 uv（`apps/api/pyproject.toml` + `uv.lock`）；前端 pnpm | |
| 指令入口 | root `justfile` | 所有 lint / test 指令都帶路徑參數 |
| 部署 | Railway（api service + web service + Postgres）+ Cloudflare R2 | api 不開 public domain，由 web 的 nginx 經 Railway private network 反代 `/api` 與 `/api/ws`，cookie 同源（避免 LINE webview 擋第三方 cookie）；web → api 只走 IPv6（nginx resolver `ipv4=off`，api `--host ::` 只監聽 IPv6）；api 固定單一實例、uvicorn 單 worker；migration 由 api 服務的 Railway pre-deploy command 執行（§5） |
| CI | GitHub Actions | lint / typecheck / 全量測試；integration 與 db-checks job 以 `compose.yaml` 起 Postgres 與 SeaweedFS，在乾淨 DB 上套用全部 revision |

## 3. Repo 結構

```
afterschool/
├── apps/
│   ├── api/                 # FastAPI 後端
│   │   ├── app/
│   │   │   ├── main.py      # create_app()
│   │   │   ├── core/        # config（env Settings）、db、security、errors、logging
│   │   │   ├── models/      # SQLAlchemy models（對應 alembic revision）
│   │   │   ├── schemas/     # Pydantic request/response
│   │   │   ├── repositories/# 跨 service 共用的查詢
│   │   │   ├── services/    # 業務邏輯（一個方法一個 task），直接以 SQLAlchemy 存取 DB
│   │   │   ├── api/
│   │   │   │   ├── admin/   # 後台 API（/api/admin/*）
│   │   │   │   ├── parent/  # 家長端 API（/api/parent/*）
│   │   │   │   ├── device/  # 打卡機 API（/api/device/*，NFC，目前 blocked）
│   │   │   │   └── ws/      # WebSocket
│   │   │   └── notifications/ # 分派器、頻道、事件定義
│   │   ├── alembic/         # env.py、script.py.mako、versions/（revision）
│   │   ├── alembic.ini
│   │   └── tests/           # unit/ 與 integration/
│   └── web/                 # Vue 3 前端（後台 + 家長端）
│       ├── index.html
│       ├── parent/index.html
│       └── src/
│           ├── api/ components/ composables/ layouts/ router/ stores/ views/ utils/ constants/   # 後台
│           ├── shared/      # 後台與家長端共用（型別、日期工具、http 基底）
│           └── parent/      # 家長端（api/ components/ views/ stores/ router.ts）
├── compose.yaml             # 本機 Postgres 17 + SeaweedFS（R2 模擬）
├── docs/                    # 規格、tasks、mockups
├── scripts/                 # validate_tasks.py、開發腳本
└── justfile
```

## 4. 設定值分層（env 只放基礎設施與 secret）

設定值分三層，**業務設定一律進 DB、由後台調整**，不寫在 env：

| 層 | 放什麼 | 例子 |
|---|---|---|
| env（`apps/api/.env`，Pydantic Settings） | 只放「沒有它程式起不來」或 secret 的基礎設施值 | `DATABASE_URL`（app_backend）、`MIGRATION_DATABASE_URL`（owner，只給 migration 指令讀，執行期 Settings 不含它）、`APP_SECRET_KEY`（JWT 簽章 + DB 內 secret 加密金鑰）、`APP_ENV`、`CORS_ORIGINS`、`PUBLIC_BASE_URL`、`R2_ENDPOINT_URL`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`、`R2_BUCKET`、`SENTRY_DSN` |
| DB `system_settings`（後台「系統設定」頁可改） | 營運參數，key/value + 每個 key 有 Pydantic schema 驗證 | 安親班名稱/Logo、營業時段、接送時段、作業進度預設預計完成時間、通知文案開關、LIFF ID、LINE Messaging channel token/secret（加密存放） |
| DB 參考資料表（後台各自的管理頁） | 可增刪的清單型設定 | 科目、考試類型、合作國小清單、作業項目範本、休假日 |

- 系統必要的預設資料（角色、科目、考試類型、system_settings 預設值）以 data migration（Alembic revision）建立，每個環境都一樣；沒有另外的本機 seed 檔。初始 admin 帳號由一次性 CLI `uv run python -m app.cli create-admin` 建立，不寫在任何 migration。
- 系統設定讀取走有 TTL 的 in-process cache，後台修改後立即失效該 key。
- DB 內的 secret（LINE channel token 等）以 `APP_SECRET_KEY` 衍生的金鑰做對稱加密存放，API 回傳時只回遮罩值。
- 新增設定項時：先在 `app/core/settings_registry.py` 註冊 key + schema + 預設值，再由 migration seed 預設值；**不要**為業務參數新增 env 變數。

## 5. 資料庫原則與 migration

### 連線角色

- **`app_backend`**：後端執行期唯一的 DB 角色（`DATABASE_URL`）。非 owner、非 superuser、`nobypassrls`、`noinherit`，只對業務表有明確 grant 的權限；對 `alembic_version` 沒有任何權限。整合測試同樣以 `app_backend` 實際登入，禁止改用 owner 連線繞過授權。
- **owner**（本機與 Railway 都是 `postgres`）：只用於 migration（`MIGRATION_DATABASE_URL`）與測試中明確需要 owner 的場合（DDL 探針、清表）。執行期程式碼不讀 `MIGRATION_DATABASE_URL`。
- **授權方式**：每張表的 revision 最後呼叫 `call app_private.grant_backend('public.<table>'[, '<privileges>'])`（baseline revision 提供），收回 `PUBLIC` 的全部權限並只授權 `app_backend`（預設 select / insert / update / delete；`audit_logs` 只給 select / insert 做成 append-only）。owner 建立的 function 預設不對 `PUBLIC` 開放 EXECUTE；被 CHECK、DEFAULT 或後端 SQL 直接呼叫的 function 要在 revision 內明確 `grant execute ... to app_backend`（trigger function 不需要）。**不使用 RLS**：資料只經 FastAPI 存取，存取控制在應用層（§6），DB 層以最小權限角色把關。grant 收尾 revision 與整合測試確保每張表的 ACL 只有 owner 與 `app_backend`。
- **`app_backend` 的建立與密碼**：baseline revision 以 `create role app_backend nologin ...`（不存在才建）建立，revision 內不含任何密碼。登入與密碼由 migrate 指令設定：
  - 雲端：`python -m app.cli migrate` 套用 revision 後，從 `DATABASE_URL` 取出 `app_backend` 的密碼，在客戶端先算成 SCRAM-SHA-256 verifier 再 `alter role app_backend login password '<verifier>'`（明文不進 DB log）。輪替密碼 = 改 Railway 的 `DATABASE_URL` 後重新部署。`DATABASE_URL` 的使用者不是 `app_backend`、或與 `MIGRATION_DATABASE_URL` 指向不同的 host / port / database 時，migrate 拒絕執行。
  - 本機：`just db-reset` 重建 DB 後設定固定的本機密碼 `app_backend_local`（只對 `127.0.0.1:54342` 有效，與 `apps/api/.env.example`、測試預設 URL 一致）。

### Migration（Alembic）

- 位置：`apps/api/alembic.ini`、`apps/api/alembic/env.py`、`apps/api/alembic/versions/`。檔名 `<revision>_<slug>.py`；revision id 以負責的 task 命名（例如 DB-004 → `db004`；baseline 固定為 `db001`），`down_revision` 指向實作當下的 head，維持單一線性歷史。
- revision 手寫，`upgrade()` 以 `op.execute` 寫原生 SQL；data migration 把 SQL 定義為模組常數（例如 `SEED_SQL`），測試可載入同一常數重跑驗證冪等。**forward-only**：`downgrade()` 一律 `raise NotImplementedError`；修正以新的 revision 前進，不回滾。已部署的 revision 不再修改。
- `env.py` 只讀 `MIGRATION_DATABASE_URL`；連線後設定 `lock_timeout`（DDL 等鎖有上限，逾時失敗而不是無限期卡住），並先 commit 掉 autobegin 的隱式交易再交給 Alembic（移植 ivy `alembic/env.py` 的做法）。
- **雲端執行時機：Railway pre-deploy command**（api 服務的 `railway.json`：`python -m app.cli migrate`）。不在 api 開機時跑：pre-deploy 失敗會中止這次部署、上一版部署繼續服務，不需要維護模式，且執行期行程不必開 owner 連線。migrate 以 session 級 `pg_advisory_lock` 串行化（等待有上限，逾時失敗並提示查 `pg_stat_activity`），移植 ivy `startup/migrations.py` 的 `_alembic_upgrade_lock` 與 `apply_migration_lock_timeout`；ivy 的空 DB / legacy baseline 四態偵測不移植（本專案從空 DB 以 revision 建起）。
- 本機：`just db-migrate`（對本機 DB `alembic upgrade head`）、`just db-reset`（drop / create 本機 `postgres` database → `alembic upgrade head` → 設定本機 `app_backend` 密碼）、`just db-new-migration <rev> <slug>`。這些 recipe 一律只連 `127.0.0.1:54342`。
- migration 必須向後相容於部署切換期間仍在執行的上一版 api（先加欄位、後改程式、最後才刪欄位）。
- **schema drift**：`scripts/check_schema_drift.py`（`just schema-drift`）以 Alembic `autogenerate.compare_metadata` 比對 `app.models.Base.metadata` 與套用全部 revision 後的 DB；比對表、欄位（型別、nullable）、FK、unique constraint，不比對 index、check constraint、server default、trigger 與 function（由各表的 migration 測試負責）。

### 資料慣例

- 所有 table 在 `public` schema，主鍵 `uuid default gen_random_uuid()`，時間欄位 `timestamptz`，含 `created_at` / `updated_at`（trigger 維護）。extension 安裝在 `extensions` schema。
- 軟刪除只用在有歷史意義的主檔（students、guardians、classes）：`archived_at`；交易紀錄不刪除。
- 敏感個資（學生身分證字號、健康備註）用應用層加密欄位，移植 ivy Student 醫療欄位加密做法。
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

教師端、多租戶、校車/娃娃車、學費帳務、成長冊/作品集、用藥、政府報表、HR（薪資/排班/員工出勤）、公告系統、大螢幕叫號、從其他系統轉入資料（只支援 Excel 範本匯入）、PWA 離線快取。

## 10. 檔案儲存（Cloudflare R2）

- 一個私有 bucket（不開 public access、不綁自訂網域），以 key 前綴區分用途：`leave-attachments/`、`student-photos/`、`pickup-person-photos/`。物件 key 為 `<前綴><owner_id>/<uuid4 32 碼 hex>.<ext>`，不使用使用者提供的檔名；DB 欄位只存前綴之後的路徑。
- 後端以 boto3（S3 API，path-style、region `auto`、SigV4）上傳、刪除，並簽發短效 presigned GET URL（預設 300 秒）給前端；瀏覽器只以 `<img>` / 連結開啟該 URL，不直接上傳，因此 bucket 不需要 CORS。檔案大小與格式由後端上傳驗證把關。
- 憑證：R2 API token 只授權該 bucket 的 Object Read & Write；放在 env `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`，`R2_ENDPOINT_URL`（`https://<account_id>.r2.cloudflarestorage.com`）與 `R2_BUCKET` 同屬 env。
- 本機：`compose.yaml` 的 SeaweedFS（`chrislusf/seaweedfs`，版本號 tag 固定；`weed mini` 單容器），只發佈 S3 API `127.0.0.1:54344`；filer / master / admin UI 不對外（filer UI 不經 S3 驗證即可讀寫所有檔案）。啟動時以 `-bucket` 參數建立 bucket `afterschool-local`，S3 設定只定義一組固定帳密（access key `afterschool`、secret `afterschool-local-secret`，寫在 `apps/api/.env.example`），不定義匿名 identity，未簽章的請求一律 403。
- DB 與 R2 不在同一個交易：先上傳、DB 寫入失敗時刪除剛上傳的物件；刪舊檔一律在 DB commit 之後（`run_after_commit`），刪除失敗只記 log（孤兒檔可接受，不可反過來刪了檔但 DB 回滾）。

## 11. 部署時實測（已知待驗證）

- Railway Postgres 的主版本是否為 17（`select current_setting('server_version_num')`）；不同時本機 `compose.yaml` 的映像主版本跟著改成與雲端一致。
- Railway Postgres 的 owner 角色 `postgres` 是否為 superuser（建立 `app_backend` 需要 CREATEROLE；baseline revision 依此假設）。
- Railway pre-deploy command 是否能經 private network 連到 Postgres，以及失敗時是否確實中止部署、保留舊版本。
- R2 presigned URL 在 LINE in-app browser 內能正常載入圖片與開啟 PDF（CSP `img-src` 的 R2 網域見 INFRA-033）。
