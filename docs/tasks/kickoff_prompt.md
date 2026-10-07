# 派工 Prompt

新 session 啟動實作時，第一則訊息貼：

> 請完整讀 `/Users/user/personal_project/ivy/afterschool/docs/tasks/kickoff_prompt.md`，以協調者（team-lead）身分照它的流程推進下一輪實作。

本檔只放「怎麼開始」與「目前有效的狀態與規則」。流程規則以 `docs/tasks/README.md` 為準、測試規則以 `docs/testing_conventions.md` 為準；三者有出入時以那兩份為準，並回頭修本檔。依 `CLAUDE.md` 文檔規則，本檔只反映目前狀態，不保留逐輪歷史（歷史看 `git log -- docs/tasks/kickoff_prompt.md`）。**每一輪收工前更新「目前狀態」與「下一輪建議順序」兩節。**

## 協調者的角色

- 你是協調者：查可開始的 task、派實作 agent、派 reviewer、處理回報、向使用者確認決策、維護本檔。**協調者原則上不自己寫實作程式碼**，只寫 tasks.json 的狀態欄位（認領、改派）與文件。
- 使用者決策：遇到 task 的 `open_design_questions` 或 agent 回報需要業務判斷的事，用 AskUserQuestion 一次整理多題問使用者（每題 2~4 個具體選項、附推薦），答案寫回 `docs/domain_spec.md` 與對應 task 後才讓 agent 繼續。純技術、有明顯合理預設的小事自己決定，並在回報給使用者時列出。
- 動手前必讀：`CLAUDE.md`、`docs/architecture_decisions.md`、`docs/domain_spec.md`、`docs/testing_conventions.md`、`docs/tasks/README.md`。`docs/tasks/api_index.md` 是前後端 API 速查（權威仍是 BACKEND task 的 description）。

## 目前狀態

- 共 734 個 task（INFRA 53、DB 42、BACKEND 400、FRONTEND 154、PARENT 85），NFC 相關 27 個 `blocked`；superseded 8 個（INFRA-003、007、037、038，DB-001、034、039，BACKEND-544）。
- 架構：DB 為 PostgreSQL（雲端 Railway Postgres、本機 `compose.yaml` 的 Postgres 17.11 + SeaweedFS 模擬 R2），migration 用 Alembic（forward-only、revision id = task id；已部署的 revision 不再修改，未部署的可直接修正），檔案存 Cloudflare R2，後端以最小權限角色 `app_backend` 連線、不使用 RLS。web → api 只走 IPv6（nginx resolver `ipv4=off`，INFRA-053）。細節見 `docs/architecture_decisions.md` §2~§5、§10、§11；部署流程與上線前驗證清單見 `docs/deployment.md`。
- 已 done（554）：
  - INFRA（49，全部非 superseded 的 task 都已完成）：001、002、004~006、008~036、039~053。
  - DB（37）：002~031、035~038、040~042。目前 head = db040（鏈尾 … → db038 → db025 → db028 → db040）。`apps/api/tests/integration/db/test_schema_conventions.py` 動態檢查每張表的慣例，之後新增的表由它把關。
  - BACKEND（367）：001~024、030~066、070~098、100~125、130~161、163、164、167~185、200~227、300~323、340~358、370~385、390、391、400~421、423~436、440~448、450~480、490~492、520~532、534~543、545~551。
    - 認證與權限、角色 / 員工帳號（含 activate、options）、參考資料、班級、學生（含照片、封存、匯入預覽（不受信任 xlsx 的深度防護，見下方慣例）、purge、close-out）、監護人與綁定碼、家長綁定 / refresh / logout、通知（enqueue、已讀、偏好、ws admin / parent）、出勤（建列、到班 / 離班 / 批次、改判、每日 / 月報 / 匯出、背景初始化）、請假（建立 / 取消 / 附件 / 套用與還原出勤 / 通知）、作業（看板、進度重算、ETA、手動完成、家長端）、接送（請求建立 / 回覆 / 確認 / 到達 / 取消 / 完成、代理授權建立 / 重產碼 / 核銷、佇列 / POS、自動過期）、成績（考試 CRUD、科目、登分、發布 / 取消發布、摘要、家長端）、儀表板、家長今日狀態的 service 與大部分 endpoint 已完成。第 9 輪另完成：學生 update_student / 匯入 execute / 學年升級 execute、作業項目 CRUD、代理接送 verify_code / 目視核對 / 強制完成 三組 service，學生匯入範本，家長端請假取消 / 附件上傳 / 重新產生接送碼 endpoint，以及監護人與員工啟用的並發回歸測試、`format_hm` 共用；後台 endpoint 318 / 353 / 390 / 431 / 433 / 522（批次到班、請假取消、作業進度、接送回覆 / 完成、員工重新啟用）。
  - FRONTEND（59）：001~009、020~025、030、033、034、036、037、039~046、050、051、053~056、066~068、070、072、073、080、082~084、092、093、098、123、155~157、186、196、230、236、238、260、294、295。api client（auth / notifications / referenceData / dashboard / roles / classes / guardians / settings）、stores（auth / lookups / adminWs）、設定頁元件與表單 dialog 已完成。
  - PARENT（42）：001~006、008、011、014~023、025~029、032、035、051、070、072、090、091、096、098、099、101、130、150、170、171、175、176、178、191。家長端 http（401 自動 refresh、409 視為已輪替）、LIFF、config / homework / notifications / notificationPreferences api client、通知設定頁已完成。家長端共用疊層堆疊在 `apps/web/src/parent/utils/overlayStack.ts`；事件圖示對照在 `parent/utils/notificationEventIcon.ts`。
- 設計稿（`docs/mockups/`）：47 張 approved；第 9 輪新出 13 張 draft 等使用者核可（清單與待裁定見「已知待驗證 / 待決」）。`index.html` 分「待核可 / 已核可」兩區。元件稿是元件外觀的權威，頁面稿只負責版面並引用元件稿。家長端 warning 色使用 `m3-tokens.css` 的 `--m3-warning-container` / `--m3-on-warning-container`；家長端層級 z-index：頂部列 5、sheet 10、dialog 15、snackbar 20；家長端頁面自帶左右 16px、底部 24px 內距，ParentLayout 不再加。
- 收工時尚未走完的 task：FRONTEND-038（in_review）、FRONTEND-091（in_review）（以 `--in-review` 與 tasks.json 為準；兩者都已依打回結論修正並改回 in_review，等複審：038 請 review-r9-f 只核對打回項、091 請 review-r9-u 只核對三個打回項；BACKEND 318 / 353 / 390 / 431 / 433 / 522 已由 review-r9-a 判決通過）。本機 compose 服務（Postgres、SeaweedFS）收工時**仍在執行**（用量額度用完而中斷，未執行 `just db-stop`）；新 session 先 `git status` / `git diff` 確認沒有被中斷的 agent 留下突變殘列（reviewer 突變驗證會暫時改壞原始檔，正常會在同一個 flock 內還原；有殘留一律 `git diff` 檢視後交 reviewer 或實作者處理，不要直接 commit），再決定 `just db-stop` 或沿用。
- 遠端：`origin = git@github.com:samjp6jp69898/ivy-afterschool.git`（main 追蹤 origin/main）。
- 工具版本基準：Python 3.13（uv）、TypeScript 鎖 `~6.0`、vite 8（rolldown）、vitest 5、pinia 4、vue-router 5、eslint 10、@playwright/test 1.63（本機已裝 chromium）；Postgres 映像 `postgres:17.11`、SeaweedFS `chrislusf/seaweedfs:4.48`；alembic 1.20、boto3 1.43、SQLAlchemy 2.1（`Select` 是 variadic generic）、FastAPI 0.142（`include_router` 是 lazy，`app.routes` 不攤平子路由，掃路由用 `fastapi.routing.iter_route_contexts(app.routes)` 或 `app.openapi()['paths']`）；starlette TestClient 回傳 `httpx2.Response`（測試型別標註用 httpx2），per-request `cookies=` 已棄用，改用 `headers={"Cookie": ...}` 或 `client.cookies`；TestClient 預設來源位址是 'testclient'（不是 IP），要驗 audit ip 用 `TestClient(app, client=("203.0.113.5", 50000))`；本機沒有 `psql`，DB 驗證用 `docker compose exec db psql -U postgres` 或 psycopg。
- 實作慣例（踩過的坑）：
  - ruff 禁 `datetime.now`（TID251），測試與 factory 一律用固定時間或注入的 clock；`app/models/base.py` 的 `Base.type_annotation_map` 有 `str → Text`，inet 用 `InetText`；非 naming convention 的約束名稱要在 model 明確指定；scheduled job 只 flush、由 runner commit（BACKEND-018 的 advisory lock 是交易級）；SQLSTATE 反例要設計成只違反一條約束，reviewer 會 drop 約束自證。
  - service 不 commit，例外只有兩處：BACKEND-037 rotate 重用分支、BACKEND-043 / 058 refresh 的 401 撤銷路徑。endpoint 要在錯誤回應附 cookie（例如 refresh 401 清 cookie）時用 `app/core/errors.py` 的 `error_response(exc)` 自組回應（exception handler 另建回應會丟掉 Set-Cookie）。
  - **鎖**：`with_for_update()` 遇到 `lazy="joined"` 的 relationship 要寫 `of=<Model>`；同一 session 可能已載入該列（例如 `get_current_staff` 已載入 actor 的員工與角色）時一律加 `execution_options(populate_existing=True)`，否則讀到 identity map 舊值、鎖形同虛設。「先查再寫」流程先鎖父列序列化（班級寫入鎖班級列、新增 / 改班學生對班級列取 FOR SHARE、上限計數鎖學生列）；可能讓系統失去管理者的異動先取 `advisory_xact_lock(session, 'rbac:admin_retained', 'global')`。**全專案鎖序約定**：作業進度列 → 接送請求列；代理授權列 → 接送請求列 → 出勤列；請假列 → 出勤列；批次鎖多列一律依主鍵排序（回報仍依輸入順序）。新流程不得反向，review 會以兩 session 真實函式重現 40P01。並發測試用兩條 app_backend 連線 + threading（阻塞模式），每條連線 `SET LOCAL lock_timeout`、join / wait 都要有 timeout；拿掉鎖時測試必須轉紅（lock_timeout 寫法可能被 FK KEY SHARE 混淆、identity map 弱參照驗不到 populate_existing，要用持有 ORM 參照或阻塞模式）。
  - **commit 後副作用**：`app/core/tx_hooks.py` 的 `run_after_commit` 會在 savepoint rollback 時丟棄該層 callback（BACKEND-545）；enqueue / 廣播不要放在 savepoint 內。374 / 404 的 before_commit 去重推播在 commit 前讀最終狀態（查詢失敗會讓 commit 失敗，已知）。
  - **測試資料**：整合測試 factories 的唯一欄位預設值帶「本行程隨機前綴 + 序號」；`make_student` 沒有 `withdrawn_on`，要 withdrawn 先建 active 再改。**committing 測試不得往 seed 表（roles 等）寫列**：建員工一律 `make_staff(role_code="tutor")`（`make_staff(session)` 預設會建自訂 `test_*` 角色，被中斷就留殘列；BACKEND-546 / 548 的測試仍有此寫法待改）。`owner_cleanup` 類 fixture 排在 `committing_db_session` 之前並設 `lock_timeout`；在經 SQLAlchemy pool 的連線上設 lock_timeout 一律 `SET LOCAL`。committing 測試跑完以 owner 唯讀查 staff_users / 非系統 roles / students 等確認無殘列；整表計數類測試（儀表板、過期 job）依賴共用 DB 無殘列。
  - **WebSocket 測試**：`tests/integration/api/ws/` 的 receive 一律走帶逾時的 `_recv(ws)`；跑 ws 測試外層加 `timeout`（`flock /tmp/afterschool_db.lock timeout 300 ...`），否則實作退化時會無限期佔住共用 DB 鎖。
  - 依賴 `APP_SECRET_KEY` 的測試各檔自備 setenv + cache_clear fixture（已在十多個檔案重複），新測試參照 `tests/integration/models/test_pickup_models.py`。
  - request schema 一律繼承 `app/schemas/common.py` 的 `RequestModel` / `UpdateModel` / `OutModel`；寫入 integer 欄位的數值要有上限。帶分頁的列表 endpoint 以 `app/api/admin/_query.py` 的 `query_model(model)` 驗證 query。路由註冊檔 `app/api/admin/__init__.py`、`app/api/parent/__init__.py`、`app/api/ws/__init__.py`、`app/jobs/__init__.py` 同一時間只交給一個 agent，只做追加 include；固定路徑（例如 `/options`、`/read-all`）必須先於 `/{id}`。
  - **endpoint 測試**：成功 / 422 / 401 / 403 / 業務錯誤；家長端 401 同時測無 cookie 與員工 cookie、IDOR 404 body 與不存在 id 完全相同；寫入型成功案例要在請求後重讀 DB（`expire_all` 或再 GET），拿掉 `db.commit()` 要轉紅；route audit 斷言要能被該 task 的 tdd.run `-k` 選到；middleware 已對 `/api/` 全域加 `Cache-Control: no-store`，handler 自設那行驗不出差別。單筆回應一律用 service 的組裝函式（例如 `leave_service.leave_out`），不可從列表分頁回找。
  - **稽核遮罩**：`audit_service` 以子字串遮罩 password / token / secret / code_hash / id_number / health_note，`must_change_password`、`token_version` 在精確比對白名單內照實記錄（BACKEND-547）；敏感欄位異動的 after 記欄位名清單（`{"set": [...]}`）。
  - **不受信任的上傳檔**：xlsx 匯入（BACKEND-155）在交給 openpyxl 前，以 expat 掃描器對 zip 內每個 entry 套大小 / 元素數 / 深度 / 單值長度上限，並限制 entry 數、同名、壓縮法、加密；openpyxl 載入與迭代的例外一律 422。之後處理任何上傳檔比照此深度防護。
  - Python 測試檔也要過 `just typecheck <檔>`；mypy 會沿 import 檢查到其他 agent 尚未提交的檔案，多 agent 並行時 typecheck 失敗要先確認錯誤是否在自己的檔案。
  - 前端：Element Plus 元件型別由 unplugin 自動補進 `apps/web/src/components.d.ts`，與該 task 一起 commit；el-form-item 錯誤訊息有 100ms debounce，spec 斷言前要等過；多個 el-select 的選單同時留在 DOM，以 input 的 aria-controls 找對應選單。後台 http 層把網路錯誤也正規化成 `ApiError(0, 'network_error')`。家長端 refresh 409 視為另一分頁已輪替（PARENT-004）。
  - **鎖（補充）**：`with_for_update()` + `populate_existing` 的查詢必須真的取出列（`scalar_one` / `scalars` / `unique`）；只呼叫 `session.execute(...)` 不消費結果時，列鎖有取到、`populate_existing` 卻不生效（BACKEND-550 在 521 發現並修正，全專案 25 處盤點僅此一處）。並發測試：外部鎖用 `FOR KEY SHARE` 才分得出實作「有沒有先鎖列」；驗 `populate_existing` 要事先載入並持有 ORM 參照；兩個 session 都碰同一列的案例單靠列鎖就序列化，拿掉學生列鎖不會轉紅。
  - **稽核殘列**：`app_backend` 對 `audit_logs` 沒有 DELETE（append-only）；探針與突變留下的 audit 殘列要以 owner 依 id 精準清除並複查全為 0。
  - **前端型別 / 上傳**：`just web-typecheck` 約 5 秒，批次改 `in_review` 前與審查前端批次時各跑一次（CLAUDE.md 寫「只在 PR 前跑一次」，建議放寬，見待決）；`el-table-column` 的 `#default="{ row }: { row: X }"` 需在每欄前加 `<!-- @vue-generic {X} -->`（FRONTEND-295）。axios 在預設 JSON 標頭下會把 FormData 序列化成 JSON 字串而遺失檔案，`createHttpClient` 對 FormData 請求只移除預設的 `application/json` 標頭（值含該子字串才刪；明確指定的 multipart 保留，FRONTEND-294）。
  - **驗證輸出判讀**：`validate_tasks.py` 成功訊息「OK: 無 ERROR」本身含 ERROR 字樣，判斷通過用 exit code 與整行 `^OK: 無 ERROR`，不要 `grep ERROR`（第 9 輪有兩位 agent 因此中止在 staged 狀態）。
- async 測試用 anyio 內建 pytest plugin，`apps/api/tests/conftest.py` 已統一提供 session 級 `anyio_backend = "asyncio"`。
- 專案 `.claude/settings.json`（權限 allowlist）由使用者要求建立，尚未 commit。

## 下一輪建議順序

依賴都已寫進 `depends_on`，一律用 `python3 scripts/validate_tasks.py . --ready [AREA]` 查。**先確認上一輪收尾**：`--in-review` 與 `in_progress` 的 task 以 tasks.json 為準（FRONTEND-038 請 review-r9-f 只核對打回項、FRONTEND-091 請 review-r9-u 只核對三個打回項；兩者判決落地前不要派它們的後續）。複審通過後，把這些 task `risk_notes` 中的打回紀錄改寫成目前仍成立的風險。

0. **先請使用者核可 / 裁定**（見「已知待驗證 / 待決」的第 9 輪項目）。核可後由設計稿 agent 把裁定與「稿發現的規格缺口」寫回 task description；新 session 若沒有當時的回報，請設計稿 agent 重新比對稿的 decisions 與 task description 列出缺口。
1. **BACKEND**：422（cancel_authorization）、437（等 524 done 已滿足）/ 438 / 439（admin/pickup.py）、386~389（作業項目 endpoint；388 / 389 視 379 / 380 的 `homework.window` 裁定）、162 / 165 / 166 / 533（學生 endpoint：162 要處理 update_student 回傳 `photo_url` 恆 None；165 驗收補 422 invalid_dates；166 驗收補 409 import_conflict；155 preview 對「退班 + 入學日晚於今天」的缺口先裁定）。先開 task 的重構 chore：create 用 `_parent_leave_out` 併入 `leave_service.parent_leave_out`；homework_service 提供公開 `progress_out`（給 382 / 390 / 377~380 共用）；346 / 349 請假鎖並發測試；374 / 404 before_commit 去重推播共用 helper；529 改用公開組裝函式取代私有 `_base_fields`；`test_student_service.py` 的 `make_staff(committing_db_session)`；158 對象列 FOR UPDATE 補會轉紅的測試。
2. **FRONTEND**（稿 approved 者）：026（router index）、031 / 035、047（已改用 landingPath）/ 048 / 049、057 / 059~062 / 069、237（非 UI）、240；api client 120 / 121 / 180 隨後端 endpoint。稿核可後才能開：127~132、189~202、231~235、239、261 / 262。同一時間只讓一位 UI agent 提交 `components.d.ts`。
3. **PARENT**（稿 approved 者）：007 / 010、050 / 094 / 095 / 100 / 131 / 151 / 152。稿核可後才能開：054、097、132、137 / 138、153、154。另開 chore：家長端 api client 路徑 id 一律 `encodeURIComponent`（後台都有，家長端沒有；PARENT-154 的 `/exams/:examId` 傳入 `../../../me` 會打到 `/api/parent/me`，只限 GET 本人權限）。
4. **DB / INFRA**：沒有 `--ready` 的 task。可考慮 INFRA task：每位 agent 獨立的測試 DB（共用 DB 鎖仍是整合測試與 reviewer 的瓶頸）。

## 派工方式（每一輪都照做）

1. `python3 scripts/validate_tasks.py . --ready` 查可開始的 task，決定本輪各區域要做的一批（同區域自然的一段，約 3~8 個 task）。
2. **認領**：協調者先把這批 task 改 `in_progress`、`assignee_session` 填實作 agent 名稱，commit 後再派工（不然 task 會停在 pending）。
3. **以區域為單位派實作 agent**（`Agent` 工具，`subagent_type: general-purpose`，`name` 用 `impl-<area>-r<輪次>`，同區域多位時加後綴 `impl-<area>-r<輪次><a|b|c>`）。跨區域可平行；同一區域原則上只有一個實作 agent，ready 數量多時可同時派 2~3 位，條件是協調者在派工訊息明列每位可改 / 不可碰的檔案、共用的路由註冊檔只歸一位、審查中的檔案任何人都不改。模型可依 task 的 `suggested_model` 指定（sonnet / opus / fable）。
4. 實作 agent 做完一批只能改 `in_review` 並回報；**不可以自己標 done**。
5. 收到回報、確認 commit 已落地（附 hash）後，派**全新的 reviewer**（不是實作 agent 的延續或 fork，`name` 用 `review-r<輪次>`，多位時加分工後綴），負責該批 `in_review` 的 task；同一位 reviewer 可連續審同一實作者的後續批次。reviewer 與該區域的實作 agent 不並行。
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
2. 寫 tasks.json：寫入前 `git status --porcelain -- <檔>`，被別人改過而未提交就先停下重讀；用 Python `json.load` → 改 → `json.dump(ensure_ascii=False, indent=2)` + 結尾換行；頂層 `version` +1、`last_updated` 更新；跑 `python3 scripts/validate_tasks.py .` 零 ERROR（看完整輸出或 grep ERROR，不要只看 `tail -1`，ERROR 行可能不在最後）。多個 agent 同時活動時，整段（load → dump → add → 自檢 → commit）包在 `flock /tmp/afterschool_git.lock` 內。
3. 看到別人的可疑未提交改動：停手 → 回報協調者 → **什麼都不要還原**。reviewer 不代為 commit 實作者的檔案。
4. 任何「已 commit / 工作區乾淨」的宣稱都附當下 `git log --oneline -1`。
5. 每個 Bash 呼叫用絕對路徑或 `cd <絕對路徑> && ...`；scratchpad 檔名加自己的 agent 名前綴。
6. 起 dev server / static server 用自己的 port，用完關掉；Playwright 產生的檔只刪自己的。清理行程只用自己記錄的 pid，禁止 `pkill -f` 這類可能命中別人行程的寬鬆 pattern。
7. 含反引號的內容不要用 bash heredoc 包 Python 寫入（會被當成指令替換），改寫成獨立 `.py` 檔執行；commit message 含反引號時先寫檔再 `git commit -F <檔>`。
8. commit message 用繁體中文、說明動了哪些 task id，結尾加 `Co-Authored-By` 行（與既有歷史一致）。
9. 不要把真實個資（姓名、電話、身分證）寫進測試、fixture、commit 或回報；用擬真假資料（王小明、0912-000-123）。
10. 判斷 lint / typecheck / test 是否通過一律看指令本身的 exit code；不要把它們接 `| tail` / `| head` 後再判斷（pipe 會吃掉 exit code），需要截斷輸出時先 `set -o pipefail` 或另外印出 `$?`。
11. 本機 DB / SeaweedFS（docker compose）共用：`just db-start` / `just db-reset` / `just db-migrate` / integration 測試一律包在 `flock /tmp/afterschool_db.lock` 內；實作與 review agent 不執行 `just db-stop`，由協調者收尾時關。可能掛住的測試（WebSocket、並發 / 鎖測試、突變驗證）在 flock 內再包 `timeout <秒>`，避免單一測試長時間佔住共用 DB 鎖、擋住所有 agent；協調者發現佔鎖過久的行程時通知其擁有者，必要時由協調者終止。

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
- **打回訊息常被漏接**：實作者回報「佇列已清空」或沒提到打回項時，先查 tasks.json 的 status / review.status 再決定是否重送；同一 agent 第二次漏接可直接改派。
- **安全類打回第二次起**：請 reviewer 先整理完整攻擊面清單（必修 / 已安全 / 待驗證 / 驗收形式），寫成檔案交實作者當驗收清單，避免逐輪釋出（本輪 BACKEND-155 走了四輪）。
- **kickoff 宣稱「稿已核可」前先查證**：`grep -l 'preview-tasks" content="[^"]*<task id>' docs/mockups/*.html` 確認稿的 preview-tasks 真的含該 task（只 grep task id 會命中 index.html 或順帶提及的稿）。
- **使用者裁定的推薦選項**：AskUserQuestion 時稿通常已照推薦選項畫，問完要另外確認「整張稿是否核可」再改 approved。

## 已知待驗證 / 待決（不要當成新發現）

- **第 9 輪待使用者裁定**：
  - 業務 / 規格：①379 / 380（修改 / 刪除作業項目）要不要受 `homework.window` 限制（推薦：只限制新增，domain_spec 改寫「新增」；若要限制，影響 update_item / delete_item 與 388 / 389）；②暫停（suspended）學生的家長端寫入（接送請求、請假）是否擋（現況只擋 withdrawn，與 ChildContextHeader 稿 decision 10 矛盾；推薦：新 BACKEND task 擋、409 student_not_active）；③考試每格備註後台要不要輸入（後端支援、家長端顯示；FRONTEND-237 送出不帶 note 會把既有 note 洗成 NULL）；④未來請假日要不要顯示在家長出勤月曆（BACKEND-342 只套用今天、303 當天才建 leave 列）；⑤155 preview 對「退班 + 入學日晚於今天」的缺口（範本沒有退班日欄，execute 才以 409 import_conflict 擋下：155 補檢查，或範本加退班日欄）。
  - 稿內待裁定（稿已照推薦畫）：parent-page-pickup 已接走後（推薦只放「需要再次接送？」按鈕）；parent-page-attendance 統計「未登記」含今天未到班（推薦照後端數字加說明）；page-attendance-monthly 統計欄排序（推薦可排序）；page-leaves 今天才開始的請假取消 dialog（推薦只顯示「將取消整筆請假」）；page-exam-detail 發布時有未儲存格（推薦先送出、仍失敗就不發布）與「未填」算法（推薦只算名單內無分數且未缺考，需新 BACKEND task 修 `get_exam_summary.missing_count`）；page-pickup-pos / page-pickup-authorizations 頂部不放 PageHeader（推薦；189 / 200 拿掉 PageHeader 字樣與對 FRONTEND-039 的依賴）。
  - 已核可稿被改動：page-settings-roles（FRONTEND-070）的 cannot_grant 由 toast 改為表單頂端可關閉 alert（commit 7123e9c，稿仍標 approved；使用者不同意則還原稿並改實作）。
  - 規則：CLAUDE.md「just web-typecheck 只在 PR 前跑一次」讓 9 個既有 vue-tsc 錯誤累積到本輪才發現；建議放寬為批次 in_review 前與審查時各跑一次（需使用者同意才改 CLAUDE.md）。
- **待核可設計稿（draft；只做靜態檢查與 happy-dom 煙霧測試，未做真實瀏覽器 live 驗證）**：後台 page-attendance-monthly、page-leaves、page-dashboard、page-exams、page-exam-detail、page-pickup-pos、page-pickup-authorizations；家長端 parent-page-child-hub、parent-page-pickup、parent-page-attendance、parent-page-leaves（併入 PARENT-137 的 LeaveDetailSheet）、parent-page-exams、parent-page-exam-detail。核可前先用瀏覽器開稿，或請一位 agent 序列跑 Playwright 四訊號檢查。稿發現的規格缺口要在核可後寫回 task：FRONTEND-127 的 `totals.service_days` 其實是應出勤人次不是營業日數；129 的 AdminListToolbar 固定有搜尋框但 API 無關鍵字；195 的狀態標籤要用 POS 對照（pending →「等待確認」、needs_reply 另加「待回覆」）；183 的 leave_type 是代碼；237 的 `student_not_in_roster` 應為 `student_not_in_exam`；233 漏 items 1~20 與 422 invalid_subject；261 / 262 的上色與「相對時間」；PARENT-137 的 editable 規則、133 的附件 url 可為 null（135 補 null 顯示）、138 / 153 載入更多要以 id 去重、097 / 052 / 132 / 138 的新文案與行為。
- **NFC 打卡**：27 個 task blocked（DB-032/033、BACKEND-500~518、FRONTEND-270~275），機型、通訊方式、刷卡判斷規則、離線佇列、一生一卡等待機器到貨後決定。`python3 scripts/validate_tasks.py . --blocked` 列出全部問題。解除時 Alembic revision id 用 `db032` / `db033`，`down_revision` 指向當下的 head，`nfc:manage` 權限由 BACKEND-510 加入。
- **部署時實測**（清單同步在 `docs/deployment.md` 上線前驗證一節）：web → api 已由 nginx resolver `ipv4=off` 固定走 IPv6（INFRA-053），Railway healthcheck 是否也經 IPv6 連到 api（`--host ::` 只監聽 IPv6）仍待實測（INFRA-031 待決問題 (3)）；Railway edge 的來源 IP 範圍與標頭（INFRA-031 / 033，決定 `TRUSTED_EDGE_CIDRS` 與 `FORWARDED_ALLOW_IPS`，實測前寧窄勿寬）；`docs/architecture_decisions.md` §11 的四項（Railway Postgres 主版本是否為 17、`postgres` 是否為 superuser、pre-deploy 能否經 private network 連 DB 且失敗時中止部署、R2 presigned URL 在 LINE in-app browser 能否載入）。另外：雲端是否可用 `btree_gist` 且裝在 `extensions` schema（DB-018 的 exclusion constraint 寫死 `extensions.gist_uuid_ops`，裝在別的 schema 會讓 migration 失敗）；家長端 ParentBottomSheet / ConfirmDialog 的 body `overflow:hidden` 在舊版 iOS / LINE WebView 是否擋得住觸控捲動，疊層焦點還原在真實瀏覽器（含 leave 動畫期間）是否正確（目前只在 happy-dom 驗證）。
- **SeaweedFS 映像固定 tag**：`compose.yaml` 的 `chrislusf/seaweedfs` 以版本號 tag 固定，不可用 `latest` / `dev`；升版時確認 `weed mini` 的 `-bucket` / `-s3.config` 參數與 healthcheck 端點仍相容。
- **測試基礎設施的已知殘留**（不擋實作，INFRA-021 或相關 task 時評估）：justfile 的全量執行防護可被刻意構造的路徑繞過（`apps/api/tests/unit/..`、`apps/api//tests`），`just web-test` 的位置參數是 vitest filter（多帶 `src` 會跑全部 spec）；unit 測試明示 `enable_socket` 仍可連本機 DB；loopback 守衛不檢查 port（127.0.0.1 上其他專案的 DB 仍可被指到）；只把 `local_db_url` 交給非 libpq 客戶端時沒有連線後複驗。
- **DB function 的 EXECUTE 權限**：baseline revision（DB-041）以全域 default privileges 收回 postgres 新建 function 對 PUBLIC 的 EXECUTE。trigger function 不受影響；但若 function 被 CHECK 約束、DEFAULT 運算式或後端 SQL 直接呼叫，migration 必須明確 `grant execute on function ... to app_backend`，否則 app_backend 寫入 / 呼叫會 42501。`app_backend` 對 `extensions` schema 沒有 USAGE（目前沒有 task 從 SQL 呼叫 pgcrypto；DB-018 的 btree_gist exclusion constraint 實作時要實測 app_backend 寫入）。
- **macOS bash 3.2**：`$var` 後緊接全形字元會被當成變數名的一部分（unbound variable），bash 腳本 / justfile 一律寫 `${var}`。
- **schema drift**：全部已建立的業務表都有 model；`PENDING_MODEL_TABLES` 只剩 NFC 的 devices、nfc_cards（DB 尚未建表）。
- **DB-040 的檢查範圍**（不擋實作）：VERIFY_SQL 與 ACL 測試只看表層級權限，column-level grant（`grant select (id) on ... to public`）偵測不到（db040 的 revoke 迴圈會清掉，目前 schema 也沒有）；不檢查 app_backend 的 with grant option；FK 索引檢查不過濾 `indisvalid = false`（本專案不用 concurrently）。日後要補時以新 task 擴充 `test_schema_conventions.py` / `test_grants_baseline.py`。
- **規劃 review 留下的 low 項目**（不擋實作，可在相關 task 實作時順手處理或之後開票）：沒有跨家長端與後台、走真實後端的接送核心流程 e2e（目前兩端各自 mock）；少數後端工具模組 task 一次包多個函式（BACKEND-223、224、013、038、404 等）。
- **鎖與並發的已知殘留**（不擋實作）：BACKEND-303 每日初始化依 student_no 多列 INSERT 與 308 批次到班依 student_id 建列，若在當天第一次初始化的瞬間對同一批尚無出勤列的學生同時執行，理論上可能互等；406 / 345 衝突時以 `.one()` 回查既有列，若該列恰在衝突後瞬間轉終態會 500（窗口極窄）；xlsx 匯入在上限內的最壞單一請求約 +93 MB，多個上傳同時進來會疊加（目前限 `students:write` 內部帳號）。
- **多 agent 協作的已知現象**：背景 agent 常在處理完一則訊息後才讀到下一則，回報會描述收到新訊息之前的狀態；同一指示可能需要重送。協調者在 reviewer 判決落地前修改規格，會與判決交錯（本輪 BACKEND-311 / 544 即此情況）——done 之後的規格修正一律開新 task，不回頭改已核可 task 的驗收條件。
