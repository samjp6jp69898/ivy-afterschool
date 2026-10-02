# task 撰寫準則

新增或拆分 task 時遵守本文件。欄位定義與生命週期見 `docs/tasks/README.md`。

## 必讀

1. `CLAUDE.md`
2. `docs/architecture_decisions.md`
3. `docs/domain_spec.md`：欄位名、狀態值、API 路徑、權限碼、事件名的唯一依據
4. `docs/testing_conventions.md`
5. `docs/tasks/README.md`
6. `scripts/validate_tasks.py`：產出的檔案必須通過它

## 拆分粒度

- **backend：一個方法一個 task**。service 的每個公開方法、每個 endpoint handler、每個 guard / dependency 各自是一個 task。一個模組的 SQLAlchemy models 合成一個 task，一個模組的 Pydantic schemas 也合成一個 task。純 repository 查詢併入使用它的 service 方法 task；被多個 service 共用的 repository 方法才獨立成 task。
- **frontend / parent：一個元件一個 task**。每個 `.vue` 元件、view、composable、store 各自是一個 task；api client 模組以一個資源一個檔案為一個 task。
- **db：一支 migration 一個 task**（通常是一張表加上它的索引、constraint、trigger、RLS）。一份 seed 一個 task。
- **infra：一個設定檔、腳本或 CI job 一個 task**。

## 欄位寫法

```json
{
  "id": "BACKEND-001",
  "title": "動詞開頭、具體：實作 LeaveService.create_leave",
  "description": "繁體中文。寫清楚：檔案、函式簽章、輸入輸出、商業規則、錯誤碼（AppError code 與 HTTP status）、與哪些 task 的介面互動（寫 task id）。移植的話說明從 ivy 保留什麼、改掉什麼。\n\n**驗收標準**：條列，每條可對應一條 red_case。",
  "granularity_unit": "method",
  "target_path": "apps/api/app/services/leave_service.py",
  "source_ref": "BE:services/student_leave_service.py::create_leave",
  "status": "pending",
  "assignee_session": null,
  "depends_on": ["DB-012", "BACKEND-020"],
  "suggested_model": "sonnet-5",
  "risk_notes": "",
  "open_design_questions": [],
  "tdd": {
    "test_path": "apps/api/tests/unit/services/test_leave_service.py",
    "markers": ["unit"],
    "red_cases": ["test_create_leave_...：輸入 X → 期望 Y（具體值）", "..."],
    "run": "just test apps/api/tests/unit/services/test_leave_service.py -k create_leave",
    "notes": ""
  },
  "review": {"status": "pending", "reviewer": null, "notes": ""}
}
```

- `source_ref`：前綴 `BE:` 代表 `/Users/user/personal_project/ivy/ivyManageSystem-backend/`，`FE:` 代表 `/Users/user/personal_project/ivy/ivyManageSystem-frontend/`，寫成 `路徑::符號`。**一定要實際打開 ivy 原始碼，確認檔案和符號都存在才寫。**新寫的功能填 `null`。移植 task 的 description 要點出 ivy 實作中值得保留的細節，例如邊界處理、防重複；也要寫明要去掉的部分：tenant_id、教師端、幼稚園特有欄位。
- `red_cases`：
  - 一般 task 至少 2 條。
  - endpoint 至少 5 條：成功、422、401、403、業務錯誤。家長端 endpoint 另加 IDOR 案例（存取別人小孩的資料回 404）。
  - 每條寫成「測試名稱：輸入 → 期望的具體輸出」。
  - 禁止恆真測試，也禁止只斷言 mock 被呼叫。
- `run`：必須指定測試檔，並加 `-k`（pytest）或 `-t`（vitest）只選本 task 的測試。
  - 後端單元：`just test <file> -k <expr>`
  - 後端整合：`just test-int <file> -k <expr>`
  - 前端：`just web-test <相對 apps/web 的路徑> -t "<name>"`
  - 設定類：`test_path: null`，並在 `notes` 寫可執行的驗收指令。
- `suggested_model`：
  - `sonnet-5`：照規格寫。
  - `opus-5`：需要判斷或跨模組。
  - `fable-5`：認證、權限、並發、加密等高風險。
- `depends_on`：只寫直接依賴，而且必須是真實存在的 id（其他區域的 id 要去讀該區域的 tasks.json 確認）。不得循環。
- NFC 打卡（domain_spec M10）：`status: "blocked"`，`open_design_questions` 寫明待決事項；description 仍要寫目前能確定的部分與驗收標準。
- 遇到 domain_spec 沒有定義或不確定的事：
  - 小事、而且有明顯合理的預設：選預設、寫進 description，並在回報中列出。
  - 會影響使用者體驗或業務規則：寫進 `open_design_questions`（問句加兩三個選項），不要自己決定。
- 不得留下歷史敘述，只寫最終規格。文字一律用繁體中文，程式識別字維持英文。

## 寫檔

- 只寫自己負責的區域檔案 `docs/tasks/<area>/tasks.json`，不碰其他區域。發現 domain_spec 的缺口或錯誤時，寫進回報交給協調者。
- 用 Python 產生檔案：
  - 頂層格式為 `{"area": "<area>", "version": 1, "last_updated": "<UTC ISO8601 Z>", "tasks": [...]}`。
  - 用 `json.dump(ensure_ascii=False, indent=2)` 寫出，並加結尾換行。
- **每完成一個模組就寫檔一次**（讀既有檔案 → append 新 task → 寫回），不要全部想完才一次寫入。中斷後接手的人可以從檔案中已存在的最後一個 id 接續。
- 開始前先檢查檔案是否已存在：已存在就讀進來，從最後一個模組之後接續，不要覆蓋既有 task。
- id 依模組分段連號，並在回報中列出區段對照表。
- 寫完跑 `python3 scripts/validate_tasks.py .`，自己區域的 ERROR 必須為零。
- 規劃階段不要 git commit，由協調者統一 commit。

## 回報格式（繁體中文，800 字內）

1. task 總數，以及 id 區段與模組的對照表。
2. blocked task 清單。
3. `open_design_questions` 清單（task id + 問題）。
4. 自己選了預設值的小決定。
5. 發現的 domain_spec 缺口或矛盾。
6. 驗證腳本輸出的最後幾行。
