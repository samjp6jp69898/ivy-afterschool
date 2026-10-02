# CLAUDE.md — afterschool

國小安親班管理系統（單一安親班、單一租戶）。管理後台（員工）+ 家長端（LINE LIFF），涵蓋帳號角色權限、學生 / 班級 / 家長、出勤、請假、作業進度與家長等待通知、接送管理、考試成績、NFC 打卡（機器未到，blocked）。功能模組從 ivy（`../ivyManageSystem-backend`、`../ivyManageSystem-frontend`）移植改寫。DB 使用 Supabase，部署在 Railway。

## 文檔規則

**所有文檔只紀錄最終決策，舊版決策和內容在更新時需要被移除。** 文件內不保留「舊決策」「曾考慮但後來否決」等歷史內容；異動歷史交給 git log。

## 權威文件

- 架構決策：`docs/architecture_decisions.md`
- 功能 / 資料模型 / API / 權限碼 / 通知事件 / 頁面：`docs/domain_spec.md`
- 測試慣例：`docs/testing_conventions.md`
- 實作規格：`docs/tasks/<area>/tasks.json`（infra / db / backend / frontend / parent），使用規則見 `docs/tasks/README.md`（每次實作 task 前必讀）

## 開發規範：TDD 是硬性規定

- 順序固定為紅 → 綠 → 重構；每條驗收標準都要對應到具體測試；沒有測試的功能視為未完成。
- 測試斷言具體輸出值或狀態變化；禁止恆真測試與只斷言 mock 被呼叫。
- 家長端 endpoint 必測 IDOR；每個 endpoint 至少涵蓋成功 / 422 / 401 / 403 / 業務錯誤。

## 禁止全量執行 lint / typecheck / test

任何 lint、format、typecheck、test 指令都必須指定路徑、檔案或 marker（例如 `just test apps/api/tests/unit/test_x.py`、`just web-test src/x.spec.ts`）。禁止無參數的 `pytest`、`ruff check .`、`pnpm lint`、`pnpm vitest run`。唯一例外是 `just web-typecheck`，只在 PR 前跑一次。

## 設定值規則

業務參數一律放 DB `system_settings`（後台可調），在 `app/core/settings_registry.py` 註冊；env 只放基礎設施與 secret（見 `docs/architecture_decisions.md` §4）。不要為業務參數新增 env 變數。

## tasks.json 使用規則（最容易忘的幾條）

- 認領前用 `python3 scripts/validate_tasks.py . --ready [AREA]` 查可開始的 task。
- UI task（`apps/web/src/{views,components}/`、`apps/web/src/parent/{views,components}/`）動手前必須先走 `.claude/skills/ui-design-preview` 取得設計稿核可。
- **實作完成只能標 `in_review`，不可自己標 `done`**；`done` 由一個獨立、沒共享實作上下文的 reviewer agent 核可後寫入（每一輪都要做）。
- 有不確定、需要使用者決策的事：用 AskUserQuestion 問，或寫進 task 的 `open_design_questions` 並標 `blocked`，不要自己猜。
- 寫入 tasks.json 一律用 Python `json.load` → 修改 → `json.dump(ensure_ascii=False, indent=2)`；`git add` / `git commit` 帶明確檔案 pathspec，禁止 `git add -A`；改完頂層 `version` +1、`last_updated` 更新，並跑 `python3 scripts/validate_tasks.py .` 必須零 ERROR。
