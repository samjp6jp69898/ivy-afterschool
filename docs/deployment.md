# 部署（Railway + Cloudflare R2）

本文件只記錄目前有效的部署流程。架構依據見 `docs/architecture_decisions.md`（§4 設定值分層、§5 資料庫與 migration、§10 檔案儲存、§11 部署時實測）。

## 1. 拓撲

Railway 專案內三個服務，外加 Cloudflare R2：

| 服務 | 說明 |
|---|---|
| `web` | 唯一對外的 public domain。nginx 提供靜態站並把 `/api` 反代到 api。Root Directory `apps/web` |
| `api` | **不開 public domain**，只走 private network。Root Directory `apps/api`，單一實例，pre-deploy 執行 migration |
| `Postgres` | Railway Postgres，只走 private network，**不開 TCP proxy** |
| Cloudflare R2 | 私有 bucket，api 以 S3 API 存取，瀏覽器只開啟後端簽發的 presigned URL |

```
瀏覽器 / LINE LIFF
      │ https
      ▼
Railway edge ──► web（nginx：靜態檔 + /api 反代）
                    │ private network（BACKEND_URL）
                    ▼
                 api（uvicorn，單一實例）──► Postgres（private network）
                    │ https（S3 API）
                    ▼
               Cloudflare R2（私有 bucket）
```

client IP 傳遞鏈：Railway edge → nginx（`real_ip` 只信任 `TRUSTED_EDGE_CIDRS`，並以 `$remote_addr` 覆寫 `X-Forwarded-For`）→ api（uvicorn 只信任 `FORWARDED_ALLOW_IPS` 範圍內、屬於 private network 的來源）。

## 2. 環境變數清單

LINE 的 ID 與 secret **不是 env**，一律填入後台「系統設定」頁（見 §5）。業務參數也不新增 env。

### api 服務

對應 `apps/api/.env.example` 的全部 key。

| 變數 | 必填 | 說明 | 範例或來源 |
|---|---|---|---|
| `APP_ENV` | 是 | 執行環境 | `production` |
| `DATABASE_URL` | 是 | 執行期唯一 DB 角色 `app_backend` | `postgresql://app_backend:<部署者產生的密碼>@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}` |
| `MIGRATION_DATABASE_URL` | 是 | owner 連線，只給 pre-deploy 的 migrate 使用 | `${{Postgres.DATABASE_URL}}` |
| `APP_SECRET_KEY` | 是 | JWT 簽章、DB 內 secret 加密、學生敏感欄位加密 / HMAC | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `CORS_ORIGINS` | 否 | 前後端同源，留空 | （留空） |
| `PUBLIC_BASE_URL` | 是 | 對外網址，不含結尾斜線，用於 LINE 通知連結與 LIFF 導回 | `https://<web 網域>` |
| `R2_ENDPOINT_URL` | 是 | R2 S3 endpoint | `https://<account_id>.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY_ID` | 是 | R2 API token 的 Access Key ID | Cloudflare（§4） |
| `R2_SECRET_ACCESS_KEY` | 是 | R2 API token 的 Secret Access Key | Cloudflare（§4） |
| `R2_BUCKET` | 是 | bucket 名稱 | Cloudflare（§4） |
| `SENTRY_DSN` | 否 | 錯誤回報，留空即停用 | Sentry 專案設定 |
| `PORT` | 是 | 必須明確設為 `PORT=8080`（與 `apps/api/Dockerfile` 的 `ENV PORT=8080` 一致）。Railway 自動注入的 PORT 只在執行期存在，web 的 `${{api.PORT}}` 讀不到，不設會讓 `BACKEND_URL` 缺 port、`/api` 全部失敗 | `8080` |

api 映像內建 `FORWARDED_ALLOW_IPS`（預設 `fd12::/16`，Railway private network 範圍），一般不需在 Railway 覆寫。

### web 服務

| 變數 | 必填 | 說明 | 範例或來源 |
|---|---|---|---|
| `BACKEND_URL` | 是 | 反代目標；缺值時 nginx 啟動失敗（fail-fast） | `http://${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}` |
| `TRUSTED_EDGE_CIDRS` | 否 | nginx `real_ip` 信任的 Railway edge 來源範圍，預設值在 `apps/web/Dockerfile`，不得放寬成 `0.0.0.0/0` | 見「上線前驗證」 |
| `PORT` | 自動 | 由 Railway 注入 | — |

## 3. 首次建置 Railway Postgres

1. 在 Railway 專案新增 Postgres 服務。
2. 以其連線執行下列兩個查詢確認前提：
   - `select current_setting('server_version_num')`：主版本須為 17（不同時，同步修改 `compose.yaml` 的映像）。
   - `select rolsuper from pg_roles where rolname = current_user`：須為 true（baseline revision 需要建立角色）。
3. 產生 `app_backend` 密碼：`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`（URL-safe，放進 URL 不需編碼）。
4. 在 api 服務設定 `DATABASE_URL` 與 `MIGRATION_DATABASE_URL`（值見 §2，後者為 `${{Postgres.DATABASE_URL}}`）。
5. 開啟 Railway Postgres 的備份排程。

**不需要手動執行任何 SQL**：第一次部署的 pre-deploy（`python -m app.cli migrate`）會套用全部 revision、建立 `app_backend` 角色，並把 `DATABASE_URL` 中的密碼同步到 DB。

## 4. 首次建置 Cloudflare R2

1. 建立 bucket：不開 public access、不啟用 r2.dev 與自訂網域。
2. 建立 R2 API token：權限 Object Read & Write，只限該 bucket。
3. 記下 Access Key ID、Secret Access Key 與 account ID，填入 api 服務的 `R2_ENDPOINT_URL`（`https://<account_id>.r2.cloudflarestorage.com`）、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`、`R2_BUCKET`。

不需設定 CORS：瀏覽器只以 `<img>` 或連結開啟後端簽發的 presigned URL（architecture_decisions §10）。

## 5. LINE 設定

全部在 LINE Developers Console / LINE Official Account Manager 操作。

1. 建立 Provider，底下建立 **LINE Login channel**，記下 channel ID（後端驗證 id_token 用）。
2. 在 Login channel 建立 **LIFF app**：Endpoint URL `https://<web 網域>/parent/`、Size `Full`、Scopes `openid` 與 `profile`、`bot_prompt` 設為 `aggressive`（登入時詢問家長加官方帳號好友，加好友是 LINE 推播的前提），記下 LIFF ID。
3. 建立 **Messaging API channel**（即官方帳號），發行 long-lived channel access token，記下 channel secret；webhook 不啟用（本系統只主動推播）。
4. 在 Login channel 的 **Linked LINE Official Account**（bot link）選擇上一步的官方帳號，`bot_prompt` 才會生效。
5. 取得官方帳號加好友 URL（`https://line.me/R/ti/p/@<basic id>`）。
6. 以 admin 登入後台 → 系統設定：`line.liff` 填 LIFF ID、Login channel ID、加好友 URL；`line.messaging` 填 channel access token、channel secret（加密存放，畫面只顯示遮罩值）。
7. 驗證：用手機 LINE 開啟 LIFF URL，確認登入時出現加好友提示；綁定後能收到 `binding.completed` 之外的一則測試推播（例如到班通知）。

## 6. 首次建置 Railway 服務

1. 建立 web 與 api 服務，設定 Root Directory（`apps/web`、`apps/api`）與 §2 的環境變數。
2. **Config File 路徑必須在服務設定填絕對路徑**：api 填 `/apps/api/railway.json`、web 填 `/apps/web/railway.json`。Railway 的 config file 不會跟著 Root Directory 解析，沒填時兩份 railway.json 會被靜默忽略，pre-deploy migration 與 healthcheck 都不會生效。
3. `apps/api/railway.json` 的 `preDeployCommand`（`python -m app.cli migrate`）會在每次部署前執行 migration；healthcheck 路徑：api `/api/health`、web `/healthz`。
4. GitHub 連動 `main` 分支自動部署，並開啟「Wait for CI」（同一個 commit 的 CI 全綠才部署）。
5. api 第一次部署完成後，以 `railway ssh --service api` 進入 api 服務執行中的容器（不要用 `railway shell`，它是本機 subshell，解析不到 private network 的 DB），執行一次 `python -m app.cli create-admin --username <name> --display-name <name>` 建立初始管理員。

## 7. 例行部署順序

1. PR merge 到 `main`。
2. CI 全綠（含 db-checks：乾淨 DB 套用全部 revision、單一 head、schema drift）。
3. Railway 建置 api 映像。
4. pre-deploy `python -m app.cli migrate`（advisory lock 串行化）。失敗則本次部署中止、上一版部署繼續服務；依 deploy log 修正後以新 commit 重新部署。
5. 新版本通過 healthcheck 後切換。
6. 執行 `just smoke https://<web 網域>`。

migration 必須向後相容於切換期間仍在執行的上一版 api：先加欄位、後改程式、最後才刪欄位。

## 8. 回滾

Railway 對服務 redeploy 上一個成功的 deployment。migration 為 forward-only，不回滾，以新的 revision 修正（向後相容規則保證上一版程式能在新 schema 上執行）。

## 9. Secret 管理

- `APP_SECRET_KEY` 同時用於 JWT 簽章、DB 內 secret 加密與學生敏感欄位加密 / HMAC，**不可任意輪替**：輪替會讓所有登入失效、既有加密資料無法解密。外洩時的處置步驟為待辦，需另行規劃重新加密工具。
- `app_backend` 密碼輪替：產生新密碼 → 更新 `DATABASE_URL` → redeploy（pre-deploy 先把新密碼寫進 DB；切換期間上一版部署的新連線會失敗，選離峰時段）。
- `MIGRATION_DATABASE_URL` 是 owner 憑證，只以 Railway 變數參照 Postgres 服務，不複製到其他地方。
- R2 token 外洩：在 Cloudflare 撤銷並重建 token、更新 `R2_*`、redeploy。

## 10. 單一實例限制

api 的 `numReplicas` 必須為 1（`apps/api/railway.json`）。要擴充需先實作 Redis broadcaster（domain_spec §5 open question）。

## 11. 上線前驗證

以下項目需在實際部署時實測，結果確認前不視為已確認：

- [ ] Railway Postgres 主版本為 17、owner 角色為 superuser（§3 的兩個查詢）。
- [ ] Railway Postgres 可安裝 `btree_gist`，且位於 `extensions` schema：`select extnamespace::regnamespace from pg_extension where extname = 'btree_gist'` 應為 `extensions`。DB-018 的 exclusion constraint 寫死 `extensions.gist_uuid_ops`；若已裝在其他 schema，`create extension if not exists` 會跳過，pre-deploy migration 會失敗。
- [ ] pre-deploy command 能經 private network 連到 Postgres，且失敗時確實中止部署、保留舊版本。
- [ ] Railway edge 轉送到 web 時的來源 IP 範圍（決定 `TRUSTED_EDGE_CIDRS`），以及真實 client IP 放在 `X-Forwarded-For` 還是其他標頭。
- [ ] web 經 private network 連 api 的來源位址範圍（決定 `FORWARDED_ALLOW_IPS`，目前預設 `fd12::/16`，雙棧環境需加上 IPv4 範圍）。
- [ ] api 以 `--host ::` 只監聽 IPv6（待決）：Railway 2025-10-16 之後建立的環境，private DNS 同時解析 IPv4 與 IPv6，nginx 解析 `api.railway.internal` 可能先拿到 IPv4 而連線失敗。需確認 web → api 與 Railway healthcheck（`/api/health`）都能連入，雙棧處理方式部署前決定（INFRA-031 open_design_questions）。
- [ ] Railway Postgres 資料庫的 collation（`select datcollate from pg_database where datname = current_database()`）。班級、學生等中文名稱排序依 DB collation（本機 `en_US.utf8` 為字碼順序，「丙班、乙班、甲班」），不會照天干或筆畫；要固定顯示順序靠 `sort_order`。
- [ ] R2 presigned URL 在 LINE in-app browser 內能正常載入圖片與開啟 PDF。
