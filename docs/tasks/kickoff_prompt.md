# 派工 Prompt

新 session 啟動實作時，第一則訊息貼：

> 請完整讀 `/Users/user/personal_project/ivy/afterschool/docs/tasks/kickoff_prompt.md`，以協調者（team-lead）身分照它的流程推進下一輪實作。

本檔只放「怎麼開始」與「目前有效的狀態與規則」。流程規則以 `docs/tasks/README.md` 為準、測試規則以 `docs/testing_conventions.md` 為準；三者有出入時以那兩份為準，並回頭修本檔。依 `CLAUDE.md` 文檔規則，本檔只反映目前狀態，不保留逐輪歷史（歷史看 `git log -- docs/tasks/kickoff_prompt.md`）。**每一輪收工前更新「目前狀態」與「下一輪建議順序」兩節。**

## 協調者的角色

- 你是協調者：查可開始的 task、派實作 agent、派 reviewer、處理回報、向使用者確認決策、維護本檔。**協調者原則上不自己寫實作程式碼**，只寫 tasks.json 的狀態欄位（認領、改派）與文件。
- 使用者決策：遇到 task 的 `open_design_questions` 或 agent 回報需要業務判斷的事，用 AskUserQuestion 一次整理多題問使用者（每題 2~4 個具體選項、附推薦），答案寫回 `docs/domain_spec.md` 與對應 task 後才讓 agent 繼續。純技術、有明顯合理預設的小事自己決定，並在回報給使用者時列出。
- 動手前必讀：`CLAUDE.md`、`docs/architecture_decisions.md`、`docs/domain_spec.md`、`docs/testing_conventions.md`、`docs/tasks/README.md`。`docs/tasks/api_index.md` 是前後端 API 速查（權威仍是 BACKEND task 的 description）。

## 目前狀態

- 共 721 個 task（INFRA 52、DB 42、BACKEND 391、FRONTEND 152、PARENT 84），NFC 相關 27 個 `blocked`；superseded 7 個（INFRA-003、007、037、038，DB-001、034、039）。
- 架構：DB 為 PostgreSQL（雲端 Railway Postgres、本機 `compose.yaml` 的 Postgres 17.11 + SeaweedFS 模擬 R2），migration 用 Alembic（forward-only、revision id = task id；已部署的 revision 不再修改，未部署的可直接修正），檔案存 Cloudflare R2，後端以最小權限角色 `app_backend` 連線、不使用 RLS。細節見 `docs/architecture_decisions.md` §2~§5、§10、§11；部署流程與上線前驗證清單見 `docs/deployment.md`。
- 已 done（222）：
  - INFRA（48，全部非 superseded 的 task 都已完成）：001、002、004~006、008~036、039~052。repo 骨架與 justfile、compose、Alembic 骨架、db-reset、doctor、bootstrap、up / down、pre-commit hook（`just install-hooks` 尚未在共用工作樹啟用）、CI（lint、typecheck、unit、integration、db-checks、web-test、web-build、docker-build）、e2e workflow、schema drift、nginx 範本、api / web Dockerfile、兩份 railway.json（Railway 服務設定的 Config File 要填絕對路徑 `/apps/api/railway.json`、`/apps/web/railway.json`）、`scripts/smoke_deploy.sh` 與 `just smoke`、`docs/deployment.md`、README、.env.example。家長端 bundle 檢查走 vite plugin 輸出的模組圖（`dist/.vite/parent-module-graph.json`，INFRA-050）。
  - DB（37）：002~031、035~038、040~042。目前 head = db040（鏈尾 … → db038 → db025 → db028 → db040）。db040 收回 PUBLIC 對全部表 / sequence / function 的權限並以 VERIFY_SQL fail-closed 自我驗證；`apps/api/tests/integration/db/test_schema_conventions.py` 動態檢查每張表的 id uuid、created_at / updated_at、set_updated_at trigger、timestamptz、FK 欄位有索引（partial index 只有 predicate 恰為「該 FK 欄位 is not null」才算）。之後新增的表由這支整合測試把關。
  - BACKEND（79）：001~020、022、030~038、040、041、047、051、062、066、070~072、076、100、101、104、106~108、111、114、123、124、130~132、134、147、167、179、181、200~203、207、210、223、301、341、372、400、403、451、490、535~541。app/core 基礎、`app.main:create_app`、models（account、audit、reference、class、student、parent、notification）、整合測試 factories（`tests/support/factories.py`）、RefreshTokenService（issue / rotate / revoke_family_by_raw / revoke_all_for_subject）與過期清理 job、StaffAuthService.login、`get_current_staff` / `get_current_parent` / `get_optional_parent`、`get_parent_student_ids`（家長可見範圍）、SettingsService.get_setting、ServiceCalendar、PublicConfigService、LINE Messaging push client、Pydantic schemas（角色、稽核、設定、參考資料、班級、監護人、家長端小孩、通知、出勤、請假、成績、儀表板）。
  - FRONTEND（31）：001~009、020、024、030、036、039~046、056、066、084、093、123、157、186、196、236、238。
  - PARENT（27）：001~003、011、014~023、025~029、032、035、051、096、098、099、101、175。家長端共用疊層堆疊在 `apps/web/src/parent/utils/overlayStack.ts`（ParentBottomSheet、ConfirmDialog 共用：最上層才處理 Esc / Tab、body 捲動鎖計數、焦點還原）；之後的 overlay 元件都要接它。
- 設計稿（`docs/mockups/`，全部 approved）：component-common、component-common-extra、component-settings、component-exams、component-pickup-countdown、component-student-select、page-admin-shell、page-students、page-attendance-today、page-homework-board、page-error、page-settings-system、page-settings-roles、page-settings-audit、parent-component-m3-kit、parent-component-bottom-sheet、parent-component-pull-to-refresh、parent-component-skeleton、parent-component-status-pill、parent-component-confirm-dialog、parent-component-empty-state、parent-component-bind-code-error、parent-component-add-friend-card、parent-component-arrival-time-picker、parent-component-pickup-person-form、parent-component-pickup-code-card、parent-component-child-context-header、parent-component-homework-item-row、parent-component-pickup-reply-message、parent-component-pickup-progress-steps、parent-component-pickup-person-list-item、parent-component-authorization-list-item。元件稿是元件外觀的權威，頁面稿只負責版面並引用元件稿。家長端 warning 色使用 `m3-tokens.css` 的 `--m3-warning-container` / `--m3-on-warning-container`（琥珀色）；家長端層級 z-index：頂部列 5、sheet 10、dialog 15、snackbar 20。
- schema drift：剩 11 張表 `table_missing_in_model`（exam_*、homework_*、pickup_*、student_attendances、student_leave*），隨 BACKEND model task 消解。
- 沒有 `in_progress` / `in_review` 殘留。本機 compose 服務已停止（`just db-start` 啟動）。
- 工具版本基準：Python 3.13（uv）、TypeScript 鎖 `~6.0`、vite 8（rolldown）、vitest 5、pinia 4、vue-router 5、eslint 10、@playwright/test 1.63（本機已裝 chromium）；Postgres 映像 `postgres:17.11`、SeaweedFS `chrislusf/seaweedfs:4.48`；alembic 1.20、boto3 1.43、SQLAlchemy 2.1（`Select` 是 variadic generic）、FastAPI 0.142（`include_router` 是 lazy，`app.routes` 不攤平子路由，測路由掛載用 `app.openapi()['paths']` 或實際請求）；starlette TestClient 回傳 `httpx2.Response`（測試型別標註用 httpx2），per-request `cookies=` 已棄用，改用 `headers={"Cookie": ...}` 或 `client.cookies`；本機沒有 `psql`，DB 驗證用 `docker compose exec db psql -U postgres` 或 psycopg。
- 實作慣例（踩過的坑）：
  - ruff 禁 `datetime.now`（TID251），測試與 factory 一律用固定時間或注入的 clock；`app/models/base.py` 的 `Base.type_annotation_map` 有 `str → Text`，inet 用 `InetText`；非 naming convention 的約束名稱要在 model 明確指定；scheduled job 只 flush、由 runner commit（BACKEND-018 的 advisory lock 是交易級）；SQLSTATE 反例要設計成只違反一條約束，reviewer 會 drop 約束自證。
  - 整合測試 factories 的唯一欄位預設值帶「本行程隨機前綴 + 序號」，DB 整合測試（integration/db）的 `make_*` 預設值固定，同一測試建多筆要自己給唯一值。committing 測試不得往 seed 表（roles 等）寫列，改用 seed 系統角色（例如 `role_code="tutor"`）；`owner_cleanup` 類 fixture 要排在 `committing_db_session` 之前（先 close session 再刪列）並設 `lock_timeout`，否則中斷或突變測試會卡鎖、留下殘留列污染共用 DB。
  - request schema 一律繼承 `app/schemas/common.py` 的 `RequestModel`（extra='forbid'，以非遞迴走訪拒絕任何字串 / bytes 含 NUL）、修改用 `UpdateModel`（至少一欄、只有 `nullable_fields` 可給 null）、輸出用 `OutModel`；寫入 integer 欄位的數值要有上限（`SortOrder` = 0~2147483647）。schema 收、DB 拒會變 DataError → 500，BACKEND-542 加的 422 handler 只是第二道防線。
  - Python 測試檔也要過 `just typecheck <檔>`（CI 的 mypy 只跑 app 與 scripts，但 review 要求動到的檔案都乾淨）。
- async 測試用 anyio 內建 pytest plugin，`apps/api/tests/conftest.py` 已統一提供 session 級 `anyio_backend = "asyncio"`。
- 專案 `.claude/settings.json`（權限 allowlist）由使用者要求建立，尚未 commit。

## 下一輪建議順序

依賴都已寫進 `depends_on`，一律用 `python3 scripts/validate_tasks.py . --ready [AREA]` 查。下一輪（第 7 輪）：

1. **BACKEND**（`--ready` 24 個，可分兩個檔案不重疊的實作 agent）：先做 023（API 整合測試 fixtures：api_client、登入 helper，endpoint 測試都靠它）、039（認證 schemas）、073 / 074（權限 dependency 與防自我提權）、180（家長端 IDOR dependency）、102（AuditService.record）、542（DataError → 422）；接著認證其餘（043 refresh、045 logout、049 change_password、052 liff_login）、service（077、109、115、182、219）、schemas（148、371）、024（schema drift 整合測試）、133、204、208（outbox dispatcher；注意 BACKEND-209 risk_notes 的 LINE client 未提供 close）、225（WebSocket 共用）、313（月出勤 Excel）。認證 endpoint 落地後 FRONTEND / PARENT 的 api client 與 view 才會陸續 ready。BACKEND-110、310、312、491 的 risk_notes 有本輪 schemas 審查留下的實作注意事項。
2. **DB**：沒有 `--ready` 的 task，等依賴解鎖。
3. **FRONTEND**：沒有 `--ready` 的 task。可派設計稿 agent 出下一批：page-login、page-change-password、page-settings-accounts、page-settings-reference、page-classes（之後再 page-attendance-monthly、page-leaves、page-pickup-authorizations、page-pickup-pos、page-exams、page-exam-detail、page-dashboard）。
4. **PARENT**：沒有 `--ready` 的 task。可派設計稿 agent 出下一批元件稿：attendance-calendar、leave-list-item、leave-attachment-list、exam-list-item、exam-score-table、connection-banner、notification-list-item、today-status-card、leave-form（之後再頁面稿）。
5. **INFRA**：全部完成。部署前要使用者決定 INFRA-031 待決問題 (3)（Railway private DNS 雙棧解析而 api 只監聽 IPv6），連同 `docs/deployment.md` 的上線前驗證清單一起處理。

## 派工方式（每一輪都照做）

1. `python3 scripts/validate_tasks.py . --ready` 查可開始的 task，決定本輪各區域要做的一批（同區域自然的一段，約 3~8 個 task）。
2. **認領**：協調者先把這批 task 改 `in_progress`、`assignee_session` 填實作 agent 名稱，commit 後再派工（不然 task 會停在 pending）。
3. **以區域為單位派實作 agent**（`Agent` 工具，`subagent_type: general-purpose`，`name` 用 `impl-<area>-r<輪次>`）。跨區域可平行；**同一區域同一時間只有一個實作 agent**。模型可依 task 的 `suggested_model` 指定（sonnet / opus / fable）。
4. 實作 agent 做完一批只能改 `in_review` 並回報；**不可以自己標 done**。
5. 收到回報、確認 commit 已落地（附 hash）後，派**一個全新的 reviewer**（不是實作 agent 的延續或 fork，`name` 用 `review-r<輪次>`），負責本輪所有 `in_review` 的 task。reviewer 與該區域的實作 agent 不並行。
6. 通過 → reviewer 寫 `done` + `review.status = pass`；打回 → `in_progress` + `review.status = concern`，問題追加進 `risk_notes`，協調者轉給原實作 agent（`SendMessage`）修正後再送審。複審可沿用同一位 reviewer，只核對打回項。複審通過後，協調者把該 task `risk_notes` 中的打回紀錄改寫成目前仍成立的風險（文件只記最終狀態）。
7. 一輪結束：`python3 scripts/validate_tasks.py .` 零 ERROR、`--in-review` 沒有殘留；更新本檔「目前狀態」「下一輪建議順序」並 commit；向使用者回報本輪完成的 task、打回次數、新的待決問題。

### 實作 agent 派工訊息範本

```
你是 afterschool 專案的 <AREA> 實作者（session 名稱 impl-<area>-r<N>）。專案根目錄 /Users/user/personal_project/ivy/afterschool。
先讀 CLAUDE.md、docs/tasks/README.md（「實作一個 task」與「寫入 tasks.json 的規則」兩節必讀）、docs/testing_conventions.md、docs/domain_spec.md 相關章節。
本輪你負責的 task（已由協調者認領為 in_progress）：<ID 清單>。依序逐一完成：
- 讀 description / risk_notes / source_ref 指向的 ivy 原始碼（移植是依本專案規格改寫，去掉多租戶、教師端、幼稚園特有邏輯）。
- TDD：依 tdd.red_cases 寫測試 → 跑 tdd.run 確認紅 → 最小實作 → 綠 → 重構。測試 commit 與實作 commit 分開（先測試後實作）。
- 只對動到的路徑跑 lint / typecheck，禁止任何無參數的全量指令。
- 只碰你區域的檔案（見 kickoff_prompt「區域檔案界線」）；發現規格錯誤依 README「實作中發現規格有錯」處理，需要使用者決策的寫進 open_design_questions 並回報，不要自己猜。
- 完成後該 task 改 in_review（不可標 done）。
全部做完回報：每個 task 的 commit hash、tdd.run 結果（綠燈數）、偏離 description 之處與是否已修正規格、新發現的問題，並附當下 `git log --oneline -1`。
逾時或沒收到背景工作完成通知時不要空等，自己查 git log / git status 確認狀態後繼續。
```

UI task（`apps/web/src/{views,components}/`、`apps/web/src/parent/{views,components}/`）另外加一段：

```
這批包含 UI task。寫任何測試或程式碼前，先依 .claude/skills/ui-design-preview/SKILL.md 出稿（docs/mockups/），把稿的絕對路徑、涵蓋 task、decisions 重點、待裁定問題回報給協調者後停手，等使用者核可（preview-status=approved）才開始 TDD。
```

### reviewer 派工訊息範本

```
你是 afterschool 專案本輪的獨立 reviewer（session 名稱 review-r<N>），沒有參與實作。專案根目錄 /Users/user/personal_project/ivy/afterschool。
先讀 CLAUDE.md、docs/tasks/README.md（「執行方式」第 3 節是你的檢查清單）、docs/testing_conventions.md。
本輪待審：`python3 scripts/validate_tasks.py . --in-review` 列出的 task（目前是 <ID 清單>，實作 commit 範圍 <hash..hash>）。
我剛核對過的 HEAD 是 <hash>，但我的核對是會過期的快照，請你自己用 git status / git log 再驗一次；工作區有這些 task 相關的未提交檔案就停手回報，不要代為 commit、不要還原任何東西。
逐一檢查：red_cases 都有對應且斷言具體行為的測試；實跑 tdd.run 全綠且真的選到每條 red_case（pytest 用 --collect-only 帶/不帶 -k 比對，vitest 看測試名稱清單）；commit 歷史看得出測試先於實作；實作符合 description、沒有範圍擴張或漏條件；權限守衛、家長端 IDOR、secret 不外洩、輸入驗證；risk_notes 的風險有處理；UI task 有 approved 設計稿且實作與稿（含 decisions）一致。
只擋「能構造出反例、會導致錯誤行為或未驗收」的問題；觀測不到的風格問題列為殘留寫進 review.notes，不打回。
判決寫入 tasks.json（README「寫入 tasks.json 的規則」）：通過 → status=done、review={status:pass, reviewer:<你的名稱>, notes}；打回 → status=in_progress、review.status=concern，問題追加進 risk_notes。寫入前重讀被打回項對應的程式碼，已被修好的就撤回不寫。
回報：每個 task 的判決與理由、打回項清單（可直接轉給實作者）、殘留清單，附當下 `git log --oneline -1`。
```

## 區域檔案界線

| 區域 | 可以改的路徑 |
|---|---|
| INFRA | root `justfile`、`README.md`、`scripts/`（`validate_tasks.py` 除外需協調者同意）、`compose.yaml`、`apps/api/alembic.ini`、`apps/api/alembic/env.py` 與 `script.py.mako`、`.github/`、`apps/api/pyproject.toml` 與工具設定、`apps/api/tests/conftest.py` 與 `tests/support/` 基礎接線、`apps/web/` 的 package / vite / tsconfig / vitest / eslint / playwright 設定、Dockerfile、railway.json、nginx 範本、`docs/deployment.md`、`docs/testing_conventions.md` |
| DB | `apps/api/alembic/versions/`、`apps/api/tests/integration/db/` |
| BACKEND | `apps/api/app/`、`apps/api/tests/`（`integration/db/` 與 INFRA 的基礎接線除外） |
| FRONTEND | `apps/web/src/`（`src/parent/` 除外）、`apps/web/e2e/`（後台）、`docs/mockups/page-*`、`docs/mockups/component-*` |
| PARENT | `apps/web/src/parent/`、`apps/web/e2e/`（家長端）、`docs/mockups/parent-*` |

INFRA-047 收尾時會改 `apps/api/app/models/base.py` 的 docstring（跨區例外，只改註解）。

`docs/tasks/<area>/tasks.json` 各區域只改自己的；修正規格牽涉其他區域時依 README 直接改並在 commit message 列出 task id。`docs/mockups/index.html` 由出稿者更新。

## 共用 working tree 的硬規則（全體 agent）

1. `git add` / `git commit` 一律帶明確檔案 pathspec，commit 用 `git commit -m ... -- <路徑>`（只提交指定路徑，不會帶走別人已 staged 的檔案；`git rm` 之後同樣要帶路徑 commit）；**禁止 `git add -A`、`git add <目錄>`、`git commit -a`、`git stash`、`--amend`、`--no-verify`**。commit 前 `git diff --cached --stat` 確認只有自己的檔。
2. 寫 tasks.json：寫入前 `git status --porcelain -- <檔>`，被別人改過而未提交就先停下重讀；用 Python `json.load` → 改 → `json.dump(ensure_ascii=False, indent=2)` + 結尾換行；頂層 `version` +1、`last_updated` 更新；跑 `python3 scripts/validate_tasks.py .` 零 ERROR。多個 agent 同時活動時，整段（load → dump → add → 自檢 → commit）包在 `flock /tmp/afterschool_git.lock` 內。
3. 看到別人的可疑未提交改動：停手 → 回報協調者 → **什麼都不要還原**。reviewer 不代為 commit 實作者的檔案。
4. 任何「已 commit / 工作區乾淨」的宣稱都附當下 `git log --oneline -1`。
5. 每個 Bash 呼叫用絕對路徑或 `cd <絕對路徑> && ...`；scratchpad 檔名加自己的 agent 名前綴。
6. 起 dev server / static server 用自己的 port，用完關掉；Playwright 產生的檔只刪自己的。清理行程只用自己記錄的 pid，禁止 `pkill -f` 這類可能命中別人行程的寬鬆 pattern。
7. 含反引號的內容不要用 bash heredoc 包 Python 寫入（會被當成指令替換），改寫成獨立 `.py` 檔執行；commit message 含反引號時先寫檔再 `git commit -F <檔>`。
8. commit message 用繁體中文、說明動了哪些 task id，結尾加 `Co-Authored-By` 行（與既有歷史一致）。
9. 不要把真實個資（姓名、電話、身分證）寫進測試、fixture、commit 或回報；用擬真假資料（王小明、0912-000-123）。
10. 判斷 lint / typecheck / test 是否通過一律看指令本身的 exit code；不要把它們接 `| tail` / `| head` 後再判斷（pipe 會吃掉 exit code），需要截斷輸出時先 `set -o pipefail` 或另外印出 `$?`。
11. 本機 DB / SeaweedFS（docker compose）共用：`just db-start` / `just db-reset` / `just db-migrate` / integration 測試一律包在 `flock /tmp/afterschool_db.lock` 內；實作與 review agent 不執行 `just db-stop`，由協調者收尾時關。

## 協調者要注意的事

- **訊息會交錯**：agent 的回報常描述它收到你訊息之前的狀態。派 reviewer 或轉交打回前，先用 tasks.json 的 status 與 `git log` 核對現況，不要只看回報。
- **背景 agent 常漏接完成通知而閒置**：全部 agent 都 idle、又沒有對應行程在跑時，主動送訊息請它用 `git log` / `git status` 自己確認並繼續。
- **實作者 commit 了卻沒送審**也會發生：收尾前逐區比對 `git log` 與 tasks.json 狀態。
- **回報被截斷**（result truncated）：請該 agent 只補傳後半段，不要重做。
- **不要在 reviewer 進場後再叫實作者改同一批檔案**；轉達打回時給「結論」而不是可直接貼上執行的指令，並明確指定執行窗口。
- **同一類問題第二次打回**：請 reviewer 一次列出能構造的全部反例當驗收清單，避免逐輪釋出。
- **使用者裁定一律寫回文件**：`docs/domain_spec.md`（業務規則）或 `docs/architecture_decisions.md`（技術），再改 task，最後才讓 agent 動手。
- **整合測試需要本機 DB**：`just db-start` / `just db-reset --yes`（docker compose 的 Postgres 與 SeaweedFS）。測試一律以 `app_backend` 角色連線，看到有人改用 owner 繞過授權一律打回。
- **長時間背景工作**（e2e、整批測試）要求 agent 結束 5 分鐘內回報。
- **逐區域滾動審查**：不必等全部區域收工；某區域一批 commit 落地就可派 reviewer 審該區。關鍵路徑上的 task（例如 DB 鏈的前置 INFRA）可破例讓同區域實作 agent 繼續做「檔案完全不重疊」的下一件，派工時明列雙方不可碰的檔案。
- **reviewer 可以多位**：一位忙或用量上限時，另派新的獨立 reviewer（`review-r<N>-<分工>`），分區負責、各寫各區的 tasks.json；Opus 用量吃緊時改用 sonnet。
- **設計稿 agent**（`design-<area>-r<N>`）一律照 task description「設計稿關卡」指定的檔名出稿；要另訂分法先由協調者改 description。核可後由設計稿 agent 把裁定寫回 task（協調者明確授權範圍）。
- **打回紀錄由 reviewer / 協調者改寫**：實作者修正打回項時不要自己刪改 risk_notes 裡的打回紀錄，複審通過後才改寫成目前仍成立的風險；轉交複審時把原打回清單附給 reviewer。
- **kickoff 宣稱「稿已核可」前先查證**：`grep -l 'preview-tasks" content="[^"]*<task id>' docs/mockups/*.html` 確認稿的 preview-tasks 真的含該 task（只 grep task id 會命中 index.html 或順帶提及的稿）。
- **使用者裁定的推薦選項**：AskUserQuestion 時稿通常已照推薦選項畫，問完要另外確認「整張稿是否核可」再改 approved。

## 已知待驗證 / 待決（不要當成新發現）

- **NFC 打卡**：27 個 task blocked（DB-032/033、BACKEND-500~518、FRONTEND-270~275），機型、通訊方式、刷卡判斷規則、離線佇列、一生一卡等待機器到貨後決定。`python3 scripts/validate_tasks.py . --blocked` 列出全部問題。解除時 Alembic revision id 用 `db032` / `db033`，`down_revision` 指向當下的 head，`nfc:manage` 權限由 BACKEND-510 加入。
- **部署時實測**（清單同步在 `docs/deployment.md` 上線前驗證一節）：Railway private DNS 對 2025-10-16 之後建立的環境同時解析 IPv4 / IPv6，而 api 以 `--host ::` 只監聽 IPv6，部署前要決定雙棧做法（INFRA-031 待決問題 (3)）；Railway edge 的來源 IP 範圍與標頭（INFRA-031 / 033，決定 `TRUSTED_EDGE_CIDRS` 與 `FORWARDED_ALLOW_IPS`，實測前寧窄勿寬）；`docs/architecture_decisions.md` §11 的四項（Railway Postgres 主版本是否為 17、`postgres` 是否為 superuser、pre-deploy 能否經 private network 連 DB 且失敗時中止部署、R2 presigned URL 在 LINE in-app browser 能否載入）。另外：雲端是否可用 `btree_gist` 且裝在 `extensions` schema（DB-018 的 exclusion constraint 寫死 `extensions.gist_uuid_ops`，裝在別的 schema 會讓 migration 失敗）；家長端 ParentBottomSheet / ConfirmDialog 的 body `overflow:hidden` 在舊版 iOS / LINE WebView 是否擋得住觸控捲動，疊層焦點還原在真實瀏覽器（含 leave 動畫期間）是否正確（目前只在 happy-dom 驗證）。
- **SeaweedFS 映像固定 tag**：`compose.yaml` 的 `chrislusf/seaweedfs` 以版本號 tag 固定，不可用 `latest` / `dev`；升版時確認 `weed mini` 的 `-bucket` / `-s3.config` 參數與 healthcheck 端點仍相容。
- **測試基礎設施的已知殘留**（不擋實作，INFRA-021 或相關 task 時評估）：justfile 的全量執行防護可被刻意構造的路徑繞過（`apps/api/tests/unit/..`、`apps/api//tests`），`just web-test` 的位置參數是 vitest filter（多帶 `src` 會跑全部 spec）；unit 測試明示 `enable_socket` 仍可連本機 DB；loopback 守衛不檢查 port（127.0.0.1 上其他專案的 DB 仍可被指到）；只把 `local_db_url` 交給非 libpq 客戶端時沒有連線後複驗。
- **DB function 的 EXECUTE 權限**：baseline revision（DB-041）以全域 default privileges 收回 postgres 新建 function 對 PUBLIC 的 EXECUTE。trigger function 不受影響；但若 function 被 CHECK 約束、DEFAULT 運算式或後端 SQL 直接呼叫，migration 必須明確 `grant execute on function ... to app_backend`，否則 app_backend 寫入 / 呼叫會 42501。`app_backend` 對 `extensions` schema 沒有 USAGE（目前沒有 task 從 SQL 呼叫 pgcrypto；DB-018 的 btree_gist exclusion constraint 實作時要實測 app_backend 寫入）。
- **macOS bash 3.2**：`$var` 後緊接全形字元會被當成變數名的一部分（unbound variable），bash 腳本 / justfile 一律寫 `${var}`。
- **schema drift 過渡紅燈**：已建立的業務表在 `app.models` 補齊前，`just schema-drift` 與 CI db-checks job 會報 table_missing_in_model，屬預期狀態，隨 BACKEND model task 消解（目前剩 11 張）。
- **DB-040 的檢查範圍**（不擋實作）：VERIFY_SQL 與 ACL 測試只看表層級權限，column-level grant（`grant select (id) on ... to public`）偵測不到（db040 的 revoke 迴圈會清掉，目前 schema 也沒有）；不檢查 app_backend 的 with grant option；FK 索引檢查不過濾 `indisvalid = false`（本專案不用 concurrently）。日後要補時以新 task 擴充 `test_schema_conventions.py` / `test_grants_baseline.py`。
- **規劃 review 留下的 low 項目**（不擋實作，可在相關 task 實作時順手處理或之後開票）：沒有跨家長端與後台、走真實後端的接送核心流程 e2e（目前兩端各自 mock）；少數後端工具模組 task 一次包多個函式（BACKEND-223、224、013、038、404 等）。
