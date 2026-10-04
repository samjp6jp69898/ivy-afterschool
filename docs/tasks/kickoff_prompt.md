# 派工 Prompt

新 session 啟動實作時，第一則訊息貼：

> 請完整讀 `/Users/user/personal_project/ivy/afterschool/docs/tasks/kickoff_prompt.md`，以協調者（team-lead）身分照它的流程推進下一輪實作。

本檔只放「怎麼開始」與「目前有效的狀態與規則」。流程規則以 `docs/tasks/README.md` 為準、測試規則以 `docs/testing_conventions.md` 為準；三者有出入時以那兩份為準，並回頭修本檔。依 `CLAUDE.md` 文檔規則，本檔只反映目前狀態，不保留逐輪歷史（歷史看 `git log -- docs/tasks/kickoff_prompt.md`）。**每一輪收工前更新「目前狀態」與「下一輪建議順序」兩節。**

## 協調者的角色

- 你是協調者：查可開始的 task、派實作 agent、派 reviewer、處理回報、向使用者確認決策、維護本檔。**協調者原則上不自己寫實作程式碼**，只寫 tasks.json 的狀態欄位（認領、改派）與文件。
- 使用者決策：遇到 task 的 `open_design_questions` 或 agent 回報需要業務判斷的事，用 AskUserQuestion 一次整理多題問使用者（每題 2~4 個具體選項、附推薦），答案寫回 `docs/domain_spec.md` 與對應 task 後才讓 agent 繼續。純技術、有明顯合理預設的小事自己決定，並在回報給使用者時列出。
- 動手前必讀：`CLAUDE.md`、`docs/architecture_decisions.md`、`docs/domain_spec.md`、`docs/testing_conventions.md`、`docs/tasks/README.md`。`docs/tasks/api_index.md` 是前後端 API 速查（權威仍是 BACKEND task 的 description）。

## 目前狀態

- 共 715 個 task（INFRA 49、DB 42、BACKEND 388、FRONTEND 152、PARENT 84），NFC 相關 27 個 `blocked`；superseded 7 個（INFRA-003、007、037、038，DB-001、034、039）。
- 架構：DB 為 PostgreSQL（雲端 Railway Postgres、本機 `compose.yaml` 的 Postgres 17.11 + SeaweedFS 模擬 R2），migration 用 Alembic（forward-only、revision id = task id），檔案存 Cloudflare R2，後端以最小權限角色 `app_backend` 連線、不使用 RLS。細節見 `docs/architecture_decisions.md` §2~§5、§10、§11。
- 已 done（138）：
  - INFRA（38）：001、002、004~006、008~028、031、033、034、041~049。repo 骨架與 justfile、compose、Alembic 骨架、db-reset、doctor、bootstrap、up / down（服務自成 process group，down 對整個 group 送訊號）、pre-commit hook（`just install-hooks` 尚未在共用工作樹啟用）、CI（lint、typecheck、unit、integration、db-checks、web-test）、schema drift、nginx 範本、api / web Dockerfile、README、.env.example；Supabase 產物已移除並以掃描測試鎖住。
  - DB（18）：002、041、042、003、004~006、008~012、014、015、029、035~037。目前 head = db037（串接 db012 → db004 → db005 → db006 → db014 → db015 → db029 → db035 → db036 → db037）。
  - BACKEND（33）：001（Settings）、002（clock）、003（errors）、004（logging / request id / 遮罩）、005（db session，endpoint 測試以 `tests/support/db_override.py::override_get_db` 覆寫 get_db）、006（tx_hooks）、007、008（pagination）、009（crypto）、012（rate limit / lockout）、013~016（R2Storage、uploads）、017（advisory lock）、019（security middleware）、031、032、040、051、070、072、106、201~203、223、372、403、535~538。
  - FRONTEND（31）：001~009、020、024、030、036、039~046、056、066、084、093、123、157、186、196、236、238。
  - PARENT（18）：001~003、011、014~020、022、023、027、028、051、098、175。
- 設計稿（`docs/mockups/`，全部 approved）：component-common、component-common-extra、component-settings、component-exams、component-pickup-countdown、page-admin-shell、page-students、page-attendance-today、page-homework-board、parent-component-m3-kit、parent-component-bottom-sheet、parent-component-pull-to-refresh、parent-component-skeleton、parent-component-status-pill。元件稿是元件外觀的權威，頁面稿只負責版面並引用元件稿。家長端 warning 色使用 `m3-tokens.css` 的 `--m3-warning-container` / `--m3-on-warning-container`（琥珀色）。
- 沒有 `in_progress` / `in_review` 殘留。本機 compose 服務已停止（`just db-start` 啟動）。
- 工具版本基準：Python 3.13（uv）、TypeScript 鎖 `~6.0`、vite 8、vitest 5、pinia 4、vue-router 5、eslint 10、@playwright/test 1.63（本機已裝 chromium）；Postgres 映像 `postgres:17.11`、SeaweedFS `chrislusf/seaweedfs:4.48`；alembic 1.20、boto3 1.43、SQLAlchemy 2.1（`Select` 是 variadic generic，單一 entity 的 select 型別是 `Select[M]`）；本機沒有 `psql`，DB 驗證用 `docker compose exec db psql -U postgres` 或 psycopg。
- async 測試用 anyio 內建 pytest plugin，`apps/api/tests/conftest.py` 已統一提供 session 級 `anyio_backend = "asyncio"`。
- 專案 `.claude/settings.json`（權限 allowlist）由使用者要求建立，尚未 commit。

## 下一輪建議順序

依賴都已寫進 `depends_on`，一律用 `python3 scripts/validate_tasks.py . --ready [AREA]` 查。下一輪（第 5 輪）：

1. **BACKEND**：SQLAlchemy model 優先（030 account、101 audit；100 reference 等 DB-007 完成），因為 `just schema-drift` 與 CI 的 db-checks job 在 model 補齊前會紅燈。接著 010（keyed_hash）、033（access token）、018（scheduler）、539（RedactingFilter 補遮罩），Pydantic schemas（076、104、111、134、167、181、210、301、341、451、490）成批做。BACKEND-020 實作時要處理 description 新增的「未處理例外的 500 回應帶 X-Request-ID 與安全標頭」。
2. **DB**：007（system_settings）、013（class_staff）、016（guardians）、018（student_leaves，btree_gist exclusion constraint 要以 app_backend 實測寫入）、021、022、023、026、030、031。
3. **INFRA**：029（bundle 檢查與 web-build job）、030（e2e workflow）、036（docker-build job）。部署段（032、035、039 與 INFRA-040 deployment.md）排在後端可跑之後。
4. **PARENT**：021 M3TopAppBar（m3-kit 稿）、025 ParentBottomSheet（bottom-sheet 稿）可直接開工；026、029、032、035、096、099、101 要先派設計稿 agent 照各 task description 指定的檔名出稿。
5. **FRONTEND**：目前沒有 `--ready` 的 task（view 等 BACKEND endpoint 與 api client）。可先派設計稿 agent 出尚未有稿的頁面（設定、帳號、考試、接送 POS 等）。
6. 部署前要使用者決定：api 的 uvicorn `--host ::` 只監聽 IPv6（INFRA-031 open_design_questions），Railway healthcheck 或 web→api 若走 IPv4 需改 `0.0.0.0`；連同 architecture_decisions §11 的實測項目一起處理。

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
- **部署時實測**：Railway edge 的來源 IP 範圍與標頭（INFRA-031 / 033，決定 `TRUSTED_EDGE_CIDRS` 與 `FORWARDED_ALLOW_IPS`，實測前寧窄勿寬）；`docs/architecture_decisions.md` §11 的四項（Railway Postgres 主版本是否為 17、`postgres` 是否為 superuser、pre-deploy 能否經 private network 連 DB 且失敗時中止部署、R2 presigned URL 在 LINE in-app browser 能否載入）。
- **SeaweedFS 映像固定 tag**：`compose.yaml` 的 `chrislusf/seaweedfs` 以版本號 tag 固定，不可用 `latest` / `dev`；升版時確認 `weed mini` 的 `-bucket` / `-s3.config` 參數與 healthcheck 端點仍相容。
- **測試基礎設施的已知殘留**（不擋實作，INFRA-021 或相關 task 時評估）：justfile 的全量執行防護可被刻意構造的路徑繞過（`apps/api/tests/unit/..`、`apps/api//tests`），`just web-test` 的位置參數是 vitest filter（多帶 `src` 會跑全部 spec）；unit 測試明示 `enable_socket` 仍可連本機 DB；loopback 守衛不檢查 port（127.0.0.1 上其他專案的 DB 仍可被指到）；只把 `local_db_url` 交給非 libpq 客戶端時沒有連線後複驗。
- **DB function 的 EXECUTE 權限**：baseline revision（DB-041）以全域 default privileges 收回 postgres 新建 function 對 PUBLIC 的 EXECUTE。trigger function 不受影響；但若 function 被 CHECK 約束、DEFAULT 運算式或後端 SQL 直接呼叫，migration 必須明確 `grant execute on function ... to app_backend`，否則 app_backend 寫入 / 呼叫會 42501。`app_backend` 對 `extensions` schema 沒有 USAGE（目前沒有 task 從 SQL 呼叫 pgcrypto；DB-018 的 btree_gist exclusion constraint 實作時要實測 app_backend 寫入）。
- **macOS bash 3.2**：`$var` 後緊接全形字元會被當成變數名的一部分（unbound variable），bash 腳本 / justfile 一律寫 `${var}`。
- **schema drift 過渡紅燈**：已建立的業務表在 `app.models` 補齊前，`just schema-drift` 與 CI db-checks job 會報 table_missing_in_model，屬預期狀態，隨 BACKEND model task 消解。
- **students.class_id 是 partial index**（`where archived_at is null`）：DB-040 的「FK 欄位都有索引」測試要決定 partial index 算不算；不算就以新 revision 補非 partial 索引。
- **規劃 review 留下的 low 項目**（不擋實作，可在相關 task 實作時順手處理或之後開票）：沒有跨家長端與後台、走真實後端的接送核心流程 e2e（目前兩端各自 mock）；少數後端工具模組 task 一次包多個函式（BACKEND-223、224、013、038、404 等）。
