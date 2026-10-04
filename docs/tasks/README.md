# tasks.json 使用與維護指南

每個接手實作的人（或 agent session）動手前必讀。只記錄目前有效的規則，異動歷史交給 git log。

## 這套系統是什麼

`docs/tasks/{infra,db,backend,frontend,parent}/tasks.json` 五個檔案共同構成完整實作規格。每個 task 是一個獨立可驗收的最小實作單位：

| 區域 | 前綴 | 最小單位 | 範圍 |
|---|---|---|---|
| infra | `INFRA-` | 一個設定檔 / 腳本 / CI job | repo 骨架、justfile、`compose.yaml`（本機 Postgres / SeaweedFS）、Alembic 設定（`alembic.ini`、`env.py`）、Python/前端專案設定、CI、Railway 部署 |
| db | `DB-` | 一支 Alembic revision（通常一張表）/ 一支 data migration | `apps/api/alembic/versions/*.py`、表的 grant、`apps/api/tests/integration/db/` |
| backend | `BACKEND-` | 一個方法（service 方法、endpoint handler、guard、model、schema 群組） | `apps/api/` |
| frontend | `FRONTEND-` | 一個元件 / view / composable / store / api client 模組 | `apps/web/src/`（後台與共用，不含 `src/parent/`） |
| parent | `PARENT-` | 一個元件 / view / composable / store / api client 模組 | `apps/web/src/parent/`（家長端） |

撰寫或新增 task 的準則見 `docs/tasks/authoring_guide.md`。新 session 要推進實作時，從 `docs/tasks/kickoff_prompt.md` 開始（協調者流程、派工與 review 訊息範本、目前狀態）。規格依據：`docs/architecture_decisions.md`（架構）、`docs/domain_spec.md`（資料模型、API、權限碼、事件、頁面）、`docs/testing_conventions.md`（測試規則）。功能從 ivy 移植時，task 的 `source_ref` 指出 ivy 的來源檔案與方法——**移植是「讀懂後依本專案規格改寫」，不是整檔複製**：去掉多租戶、教師端、幼稚園特有邏輯，DB 存取改成本專案的 repository 層。

## 建議順序

用 `python3 scripts/validate_tasks.py . --ready [AREA]` 查詢依賴已滿足的 task，不要憑感覺挑。大方向：

1. **INFRA** 的骨架（repo、justfile、`compose.yaml`、Alembic 設定、`apps/api` 與 `apps/web` 專案設定）先做完，其他區域全部直接或間接依賴它。
2. **DB** 的 baseline revision（extensions、`updated_at` trigger、`app_backend` 角色與 `grant_backend`）→ 各模組表。
3. **BACKEND** 的框架（Settings、DB session、錯誤處理、認證、權限守衛）→ 各模組 service → endpoint。
4. **FRONTEND / PARENT** 在對應 BACKEND endpoint 的形狀穩定後開始；UI task 先過設計稿關卡。
5. `blocked` 的 task（NFC 打卡等）等 `open_design_questions` 有答案再處理：`--blocked` 可列出全部待決事項。

## task 欄位

| 欄位 | 用途 |
|---|---|
| `id` | 區域前綴 + 三位數，例如 `BACKEND-052`，全專案唯一 |
| `title` / `description` | 完整規格，`description` 是唯一權威，必含「**驗收標準**」段落 |
| `granularity_unit` | `method` / `endpoint` / `component` / `view` / `composable` / `store` / `api_client` / `migration` / `seed` / `config` / `script` / `test` / `doc` |
| `target_path` | 主要異動的檔案路徑 |
| `source_ref` | 移植來源，例如 `BE:services/student_leave_service.py::apply_attendance_for_leave`；新寫的功能為 `null` |
| `status` | 見下方生命週期 |
| `assignee_session` | 認領者識別；沒人認領為 `null` |
| `depends_on` | 前置 task id，必須全部 `done`（或 `superseded`）才能開始 |
| `suggested_model` | `sonnet-5`（照規格寫）/ `opus-5`（需要判斷或跨模組）/ `fable-5`（安全或並發等高風險） |
| `risk_notes` | 目前仍成立的實作風險；review 打回的問題追加在這裡 |
| `open_design_questions` | 真正尚未決定、需要使用者決策的問題。空陣列 = 沒有懸而未決的事 |
| `tdd` | `test_path`、`markers`（`unit`/`integration`/`vitest`/`playwright`）、`red_cases`（先寫的失敗測試，每條講清楚輸入與期望輸出）、`run`（只跑這個 task 測試的指令，禁止無參數全量指令）、`notes` |
| `review` | `status`（`pending` 尚未審 / `pass` / `concern`）、`reviewer`（reviewer session 識別）、`notes`（判決理由與發現） |

## status 生命週期

```
pending → in_progress → in_review → done
              ↑              │
              └──────────────┘（reviewer 打回）
              ↓
           blocked（寫清楚卡在哪、需要誰決定）
```

外加 `superseded`：職責已移交給另一個 task，`description` 只留一句「由 X 負責」。

- `done` 的 task 遇到架構決策變更：產物被刪除或整個由新 task 取代時改成 `superseded`（`description` 只留「由 X 負責」，`review` 保留原判決）；產物仍在、只被新 task 部分改寫時保留 `done`，由新 task 的 `description` 寫明它改寫的範圍。依賴 superseded task 的未完成 task 改依賴接手的 task。

- 認領：`status` 改 `in_progress`、`assignee_session` 填識別。
- **實作者做完只能改成 `in_review`，不可以自己標 `done`**。`done` 一律由 reviewer 核可後寫入（`review.status = pass`、`review.reviewer` 填 reviewer 識別），單人操作也一樣，沒有例外。驗證腳本會擋 `done` 但 `review.status != pass` 的狀態。
- 打回：reviewer 把 `status` 改回 `in_progress`、`review.status = concern`、在 `review.notes` 寫判決，並把要修的問題**追加**進 `risk_notes`（不覆蓋原內容）。
- 實作者可以把 `review.status` 從 `pass` 改成 `concern`（自曝問題），**不可以**把 `concern` 改成 `pass`。
- 卡住：改 `blocked`，在 `open_design_questions` 或 `risk_notes` 寫清楚。

## 實作一個 task（TDD，硬性規定）

0. **（只限 UI task）設計稿關卡**：`target_path` 在 `apps/web/src/views/`、`apps/web/src/components/`、`apps/web/src/parent/views/`、`apps/web/src/parent/components/` 的 task，先走 `.claude/skills/ui-design-preview` 出稿並取得使用者核可（稿的 `preview-status` 為 `approved`）才能進 `in_progress`：
   ```bash
   grep -l "FRONTEND-023" docs/mockups/*.html | xargs grep -h 'name="preview-status"'
   ```
1. 確認 `depends_on` 全部 `done`。
2. 讀 `description` 全文、`risk_notes`、`open_design_questions`、`source_ref` 指向的 ivy 原始碼、`docs/testing_conventions.md` 對應章節。
3. 依 `tdd.red_cases` 逐條寫測試，跑 `tdd.run` 確認全紅。
4. 最小實作讓測試轉綠，然後在測試保護下重構。
5. 只對動到的路徑跑 lint / typecheck（`just lint <path>`、`just web-lint <file>`），**絕不跑無參數的全量指令**。
6. 測試 commit 與實作 commit 分開（先測試後實作），讓 reviewer 從歷史看得出 TDD 順序。
7. `status` 改 `in_review`。

`tdd.test_path` 為 `null` 的 task（純設定），照 `tdd.notes` 寫明的可執行指令驗收。

## 執行方式：區域分工 + 獨立 reviewer（每一輪都要做）

### 1. 實作 agent 以「區域」為單位

用 `Agent` 工具派實作 agent，**一個區域原則上同一時間只有一個實作 agent**（同區域 task 常改同一批檔案）；ready 數量多時可同時派 2~3 位，條件是協調者明列每位可改 / 不可碰的檔案、共用的路由註冊檔只歸一位、審查中的檔案任何人都不改。agent 在自己的區域內依 `--ready` 清單依序完成一批 task 的 TDD 循環。不同區域的 agent 可以平行。

### 2. 實作 agent 完成一批後標 `in_review`，回報給協調者

回報內容：做了哪些 task、對應 commit hash、每個 task 的 `tdd.run` 是否全綠、有沒有偏離 `description`（有的話是否已依下一節修正規格）、附上當下的 `git log --oneline -1`。

### 3. 協調者派獨立 reviewer

**這一步強制，不能省略。** reviewer 必須是新開的 agent（不是實作 agent 的延續或 fork），只給它 task 的 `description`、`tdd` 與實際 diff。一位 reviewer 負責一批或多批 `in_review` 的 task（`--in-review` 查詢）；同時有多位實作者時可分派多位 reviewer，各自只審、只寫自己負責的 task。逐一檢查：

- `tdd.red_cases` 每一條都有對應測試，且斷言具體行為（非恆真、非只斷言 mock 被呼叫）。
- **實跑 `tdd.run`**，確認全綠且真的選到每一條 red_case（pytest 用 `--collect-only` 對照、vitest 看測試名稱清單），不可只用眼睛比對。
- commit 歷史看得出測試先於實作。
- 實作符合 `description`，沒有範圍擴張也沒有漏掉條件；`depends_on` 的介面用法正確，不是猜的。
- 安全：權限守衛有掛、家長端有 IDOR 防護、secret 不外洩、輸入有驗證。`risk_notes` 提到的風險有被實際處理。
- 只對動到的路徑跑過 lint / typecheck，且乾淨。
- （UI task）有對應的 approved 設計稿，實作與稿一致，包含稿中 `decisions` 陣列的每一條。

判決寫入：通過 → `status = done`、`review = {status: pass, reviewer, notes}`；不通過 → 見上方「打回」。**寫入判決前重讀被打回項目對應的程式碼**，寫入當下已被修好的項目就撤回不寫。

**reviewer 與被審批次的實作 agent 不並行**：實作 agent 回報收工、commit 落地（有 hash）後，reviewer 才進場（實作 agent 可同時做檔案不重疊的下一批）；reviewer 判決 commit 後，才派實作 agent 修打回項目。

### 4. 收尾

一輪結束後跑 `python3 scripts/validate_tasks.py .` 確認零 ERROR，再用 `--ready` 查下一批。

## 實作中發現規格有錯

1. 不要默默用自己的理解蓋過規格，也不要照抄確定錯的規格：**先改 `description`（和 `docs/domain_spec.md`，若牽涉欄位/API/權限碼/事件）**，讓它反映正確版本。
2. 牽動其他區域的 task 時直接改對方檔案，commit message 列出所有改動的 task id 與原因。
3. 把驗收標準移交給別的 task 時，要指名收件 task id、實際寫進對方的 `description` 與 `tdd.red_cases`、確認對方 `tdd.run` 選得到，並在自己的 `description` 註明去向——四者缺一等同刪除。
4. 遇到需要使用者決策的問題：寫進 `open_design_questions`，必要時把 task 改 `blocked`，並回報協調者向使用者確認。**不要自己猜一個答案就實作。**
5. 修改後頂層 `version` +1、`last_updated` 改為現在 UTC 時間，跑驗證。

## 寫入 tasks.json 的規則

- 一律用 Python 結構化修改：`json.load` → 修改 → `json.dump(ensure_ascii=False, indent=2)` + 結尾換行。
- 寫入前先跑 `git status --porcelain -- docs/tasks/<area>/tasks.json`；若被別人改過而未提交，先停下來重讀自己要改的 task，確認結論仍成立。
- `git add` 與 `git commit` 都帶明確檔案 pathspec，**禁止 `git add -A`、`git add <目錄>`、`git commit -a`**。commit 前跑 `git diff --cached --stat` 確認只有自己的檔案。
- 含別人未提交的變更且拆不開時，照常 commit 並在訊息註明「順帶收入 <誰> 未提交的 <什麼>」；**絕不用 `git checkout` / `git restore` 還原別人的改動**。
- 不 amend、不改寫歷史。
- 任何「工作區乾淨 / 已 commit」的宣稱都附上當下的 `git log --oneline -1`。
- 不得留下歷史敘述，只寫最終規格。

## 驗證

```bash
python3 scripts/validate_tasks.py .                 # 完整驗證，必須 OK: 無 ERROR
python3 scripts/validate_tasks.py . --ready BACKEND # 可開始的 task
python3 scripts/validate_tasks.py . --in-review     # 等 review 的 task
python3 scripts/validate_tasks.py . --blocked       # blocked 與待決問題
python3 scripts/validate_tasks.py . --stats         # 各區域進度
```
