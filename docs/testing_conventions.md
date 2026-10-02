# 測試慣例

> 所有實作 task 的共同依據。只記錄目前有效的最終決策。

## 1. TDD 是硬性規定

- 順序固定為**紅 → 綠 → 重構**。不允許先寫功能再補測試。
- task `description` 的每一條驗收標準都要對應到具體測試（檔案 + 測試名稱）。對應不到的視為未驗收，task 不得標 `done`。
- 例外：純設定檔以 `tdd.notes` 寫明的可執行驗收指令取代測試。
- 純補測試（實作本身正確、只缺測試）：用突變證明新測試會紅，不要故意把程式改壞再改回來。

## 2. 測試品質

- 每個測試斷言**具體輸出值或狀態變化**。禁止恆真測試（`assert True`、單獨的 `is not None`、`toBeTruthy()`）與只斷言 mock 被呼叫。
- 測試名稱描述行為與情境：`test_apply_leave_marks_attendance_leave_for_each_service_day`。
- 每個 service 方法至少涵蓋：正常路徑、錯誤路徑（例外型別與錯誤碼）、邊界值（跨日、休息日、空清單、`None`）。
- 每個 endpoint 至少涵蓋：成功、422（含 `extra='forbid'` 多餘欄位）、401、403（缺權限碼）、自身業務錯誤（例如 409）。
- **家長端 endpoint 必測 IDOR**：家長 A 用家長 B 小孩的 `student_id` / 資源 id 存取要回 404（不洩漏存在與否）。
- 時間相關邏輯一律注入 `app/core/clock.py` 的時鐘，不在測試裡 sleep 或依賴真實時間；「今天」以 `Asia/Taipei` 判斷，要有跨午夜（UTC 16:00）的邊界案例。
- 測資擬真但不用真實個資（學生姓名用「王小明」這類常見假名，電話用 `0912-000-xxx`）。

## 3. 測試分層

| 層級 | marker | 可碰 DB | 預設執行 | 位置 |
|---|---|---|---|---|
| 後端單元 | `unit`（未標視同 unit） | 否（repository 以 fake 取代；不用 SQLite 冒充 Postgres） | 是 | `apps/api/tests/unit/` |
| 後端整合 | `integration` | 只能碰**本地** Supabase（`127.0.0.1:54342`） | 否，需 `-m integration` | `apps/api/tests/integration/` |
| 前端元件/邏輯 | `vitest` | — | 是 | 與被測檔同目錄 `*.spec.ts` |
| 端對端 | `playwright` | 本地全套 | 否 | `apps/web/e2e/` |

- 單元測試由 `pytest-socket` 擋外部網路（只放行 loopback 給 integration）。
- 整合測試每個測試在 transaction 內執行並 rollback；需要 commit 語意的（outbox、after-commit 通知）用獨立 fixture 清表。
- migration / RLS / schema drift 的測試屬 integration。
- 前端 API 呼叫用 axios-mock-adapter，元件測試用真實 DOM 互動（`setValue`、`trigger('click')`）斷言 emit payload 與畫面文字。

## 4. 禁止全量執行

任何 lint / format / typecheck / test 指令都**必須指定路徑、檔案或 marker**：

```
just test apps/api/tests/unit/services/test_leave_service.py -k apply
just test-int apps/api/tests/integration/test_rls.py
just lint apps/api/app/services/leave_service.py
just web-test src/components/pickup/QueueCard.spec.ts
just web-lint src/components/pickup/QueueCard.vue
```

禁止：無參數 `pytest`、`pytest apps/api/tests/`、`ruff check .`、`pnpm lint`、無檔案的 `pnpm vitest run`。唯一例外 `just web-typecheck`（vue-tsc 只能全專案跑），只在 PR 前執行一次。全量執行只在 CI。

## 5. 本機環境

- Supabase 本機 port：API `54341`、DB `54342`、Studio `54343`（避開其他專案的預設 port）。
- `just db-reset` 重建本機 DB（套用全部 migration + seed）。
- 測試用員工 / 家長帳號由 integration fixture 建立，不寫在 seed.sql。
