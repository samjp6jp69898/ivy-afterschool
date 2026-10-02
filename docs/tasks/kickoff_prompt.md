# 派工 Prompt

新 session 啟動實作時，第一則訊息貼：

> 請完整讀 `/Users/user/personal_project/ivy/afterschool/docs/tasks/kickoff_prompt.md`，以協調者（team-lead）身分照它的流程推進下一輪實作。

本檔只放「怎麼開始」與「目前有效的狀態與規則」。流程規則以 `docs/tasks/README.md` 為準、測試規則以 `docs/testing_conventions.md` 為準；三者有出入時以那兩份為準，並回頭修本檔。依 `CLAUDE.md` 文檔規則，本檔只反映目前狀態，不保留逐輪歷史（歷史看 `git log -- docs/tasks/kickoff_prompt.md`）。**每一輪收工前更新「目前狀態」與「下一輪建議順序」兩節。**

## 協調者的角色

- 你是協調者：查可開始的 task、派實作 agent、派 reviewer、處理回報、向使用者確認決策、維護本檔。**協調者原則上不自己寫實作程式碼**，只寫 tasks.json 的狀態欄位（認領、改派）與文件。
- 使用者決策：遇到 task 的 `open_design_questions` 或 agent 回報需要業務判斷的事，用 AskUserQuestion 一次整理多題問使用者（每題 2~4 個具體選項、附推薦），答案寫回 `docs/domain_spec.md` 與對應 task 後才讓 agent 繼續。純技術、有明顯合理預設的小事自己決定，並在回報給使用者時列出。
- 動手前必讀：`CLAUDE.md`、`docs/architecture_decisions.md`、`docs/domain_spec.md`、`docs/testing_conventions.md`、`docs/tasks/README.md`。`docs/tasks/api_index.md` 是前後端 API 速查（權威仍是 BACKEND task 的 description）。

## 目前狀態

- 規劃完成，尚未開始實作。共 700 個 task：INFRA 41、DB 40、BACKEND 383、FRONTEND 152、PARENT 84；全部 `pending`，NFC 相關 27 個 `blocked`。
- `--ready` 目前只有 `INFRA-001`。
- 專案目錄還沒有任何程式碼（只有 docs、scripts/validate_tasks.py、.claude/skills/ui-design-preview）。

## 下一輪建議順序

依賴都已寫進 `depends_on`，一律用 `python3 scripts/validate_tasks.py . --ready [AREA]` 查，不要憑感覺挑。大方向：

1. **INFRA 骨架**：INFRA-001~007（repo、justfile、Supabase CLI、apps/api、apps/web、.env.example、check_rls）→ INFRA-008~014、041（測試基礎設施）。這段只有 INFRA 能做，派一個 INFRA 實作 agent。
2. **DB 地基**：INFRA-003 完成後開 DB-001 → DB-039（本機 app_backend 登入）→ DB-002 → 各表 migration。可與 INFRA 後段平行。
3. **BACKEND 框架**：INFRA-004/010 與 DB-001/039 完成後開 BACKEND-001~024（config、clock、errors、db session、crypto、create_app、health、測試 fixtures）→ 認證（030~066）→ RBAC（070~098、534）→ 設定（100~125）→ 學生 / 班級 / 家長（130~185、529~533）→ 通知（200~227）→ 營運模組（300~)。
4. **FRONTEND / PARENT**：INFRA-005 完成後即可開共用模組 FRONTEND-001~009 與兩邊的 app shell；頁面 task 在對應 BACKEND endpoint 完成、且設計稿核可後開工。
5. 部署段（INFRA-031~040）排在後端可跑之後；部署時要實測的事項見「已知待驗證」。

## 派工方式（每一輪都照做）

1. `python3 scripts/validate_tasks.py . --ready` 查可開始的 task，決定本輪各區域要做的一批（同區域自然的一段，約 3~8 個 task）。
2. **認領**：協調者先把這批 task 改 `in_progress`、`assignee_session` 填實作 agent 名稱，commit 後再派工（不然 task 會停在 pending）。
3. **以區域為單位派實作 agent**（`Agent` 工具，`subagent_type: general-purpose`，`name` 用 `impl-<area>-r<輪次>`）。跨區域可平行；**同一區域同一時間只有一個實作 agent**。模型可依 task 的 `suggested_model` 指定（sonnet / opus / fable）。
4. 實作 agent 做完一批只能改 `in_review` 並回報；**不可以自己標 done**。
5. 收到回報、確認 commit 已落地（附 hash）後，派**一個全新的 reviewer**（不是實作 agent 的延續或 fork，`name` 用 `review-r<輪次>`），負責本輪所有 `in_review` 的 task。reviewer 與該區域的實作 agent 不並行。
6. 通過 → reviewer 寫 `done` + `review.status = pass`；打回 → `in_progress` + `review.status = concern`，問題追加進 `risk_notes`，協調者轉給原實作 agent（`SendMessage`）修正後再送審。複審可沿用同一位 reviewer，只核對打回項。
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
| INFRA | root `justfile`、`README.md`、`scripts/`（`validate_tasks.py` 除外需協調者同意）、`supabase/config.toml`、`.github/`、`apps/api/pyproject.toml` 與工具設定、`apps/api/tests/conftest.py` 與 `tests/support/` 基礎接線、`apps/web/` 的 package / vite / tsconfig / vitest / eslint / playwright 設定、Dockerfile、railway.json、nginx 範本、`docs/deployment.md`、`docs/testing_conventions.md` |
| DB | `supabase/migrations/`、`supabase/seed.sql`、`apps/api/tests/integration/db/` |
| BACKEND | `apps/api/app/`、`apps/api/tests/`（`integration/db/` 與 INFRA 的基礎接線除外） |
| FRONTEND | `apps/web/src/`（`src/parent/` 除外）、`apps/web/e2e/`（後台）、`docs/mockups/page-*`、`docs/mockups/component-*` |
| PARENT | `apps/web/src/parent/`、`apps/web/e2e/`（家長端）、`docs/mockups/parent-*` |

`docs/tasks/<area>/tasks.json` 各區域只改自己的；修正規格牽涉其他區域時依 README 直接改並在 commit message 列出 task id。`docs/mockups/index.html` 由出稿者更新。

## 共用 working tree 的硬規則（全體 agent）

1. `git add` / `git commit` 一律帶明確檔案 pathspec；**禁止 `git add -A`、`git add <目錄>`、`git commit -a`、`git stash`、`--amend`、`--no-verify`**。commit 前 `git diff --cached --stat` 確認只有自己的檔。
2. 寫 tasks.json：寫入前 `git status --porcelain -- <檔>`，被別人改過而未提交就先停下重讀；用 Python `json.load` → 改 → `json.dump(ensure_ascii=False, indent=2)` + 結尾換行；頂層 `version` +1、`last_updated` 更新；跑 `python3 scripts/validate_tasks.py .` 零 ERROR。多個 agent 同時活動時，整段（load → dump → add → 自檢 → commit）包在 `flock /tmp/afterschool_git.lock` 內。
3. 看到別人的可疑未提交改動：停手 → 回報協調者 → **什麼都不要還原**。reviewer 不代為 commit 實作者的檔案。
4. 任何「已 commit / 工作區乾淨」的宣稱都附當下 `git log --oneline -1`。
5. 每個 Bash 呼叫用絕對路徑或 `cd <絕對路徑> && ...`；scratchpad 檔名加自己的 agent 名前綴。
6. 起 dev server / static server 用自己的 port，用完關掉；Playwright 產生的檔只刪自己的。
7. 含反引號的內容不要用 bash heredoc 包 Python 寫入（會被當成指令替換），改寫成獨立 `.py` 檔執行。
8. commit message 用繁體中文、說明動了哪些 task id，結尾加 `Co-Authored-By` 行（與既有歷史一致）。
9. 不要把真實個資（姓名、電話、身分證）寫進測試、fixture、commit 或回報；用擬真假資料（王小明、0912-000-123）。

## 協調者要注意的事

- **訊息會交錯**：agent 的回報常描述它收到你訊息之前的狀態。派 reviewer 或轉交打回前，先用 tasks.json 的 status 與 `git log` 核對現況，不要只看回報。
- **背景 agent 常漏接完成通知而閒置**：全部 agent 都 idle、又沒有對應行程在跑時，主動送訊息請它用 `git log` / `git status` 自己確認並繼續。
- **實作者 commit 了卻沒送審**也會發生：收尾前逐區比對 `git log` 與 tasks.json 狀態。
- **回報被截斷**（result truncated）：請該 agent 只補傳後半段，不要重做。
- **不要在 reviewer 進場後再叫實作者改同一批檔案**；轉達打回時給「結論」而不是可直接貼上執行的指令，並明確指定執行窗口。
- **同一類問題第二次打回**：請 reviewer 一次列出能構造的全部反例當驗收清單，避免逐輪釋出。
- **使用者裁定一律寫回文件**：`docs/domain_spec.md`（業務規則）或 `docs/architecture_decisions.md`（技術），再改 task，最後才讓 agent 動手。
- **整合測試需要本機 Supabase**：`just db-start` / `just db-reset --yes`。測試一律以 `app_backend` 角色連線，看到有人改用 owner 繞過 RLS 一律打回。
- **長時間背景工作**（e2e、整批測試）要求 agent 結束 5 分鐘內回報。

## 已知待驗證 / 待決（不要當成新發現）

- **NFC 打卡**：27 個 task blocked（DB-032/033、BACKEND-500~518、FRONTEND-270~275），機型、通訊方式、刷卡判斷規則、離線佇列、一生一卡等待機器到貨後決定。`python3 scripts/validate_tasks.py . --blocked` 列出全部問題。解除時 DB migration 用當下時間戳（不可沿用規劃時的檔名），`nfc:manage` 權限由 BACKEND-510 加入。
- **部署時實測**：Railway edge 的來源 IP 範圍與標頭（INFRA-031 / 033，決定 `TRUSTED_EDGE_CIDRS` 與 `FORWARDED_ALLOW_IPS`，實測前寧窄勿寬）；Supabase 連線池是否接受 `app_backend` 自訂角色（DB-001，不接受時依使用者裁定改用 postgres 角色，步驟見 INFRA-040）。
- **規劃 review 留下的 low 項目**（不擋實作，可在相關 task 實作時順手處理或之後開票）：沒有跨家長端與後台、走真實後端的接送核心流程 e2e（目前兩端各自 mock）；少數後端工具模組 task 一次包多個函式（BACKEND-223、224、013、038、404 等）。
