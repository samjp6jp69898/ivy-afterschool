-- DB-039：本機開發專用 seed，`just db-reset` 時在全部 migration 之後執行；雲端不會執行本檔。
--
-- 只放「本機才需要」的設定：
--   * app_backend 的本機登入與密碼。DB-001 建立的 app_backend 是 nologin、不含密碼；雲端由部署者在
--     SQL editor 一次性執行 `alter role app_backend login password '<隨機 32 字元以上>'`（見 DB-001 部署備忘）。
--     這組密碼只用於本機 Supabase（127.0.0.1:54342），對應本機
--     DATABASE_URL = postgresql+psycopg://app_backend:app_backend_local@127.0.0.1:54342/postgres
--     （與 apps/api/.env.example 一致）。
--
-- 不放的東西：
--   * 系統必要的預設資料（角色、科目、考試類型、system_settings 預設值）由 data migration 建立（DB-035~038）。
--   * 任何員工 / 家長帳號與密碼雜湊。測試帳號由 integration fixture 建立；初始 admin 由
--     `uv run python -m app.cli create-admin` 一次性建立（BACKEND task）。

alter role app_backend login password 'app_backend_local';
