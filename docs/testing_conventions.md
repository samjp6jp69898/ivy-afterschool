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
- **endpoint 整合測試的測資先 commit 再打 API**：`get_db` 以 BACKEND-005 的 `override_get_db(db_session)` 覆寫，每個請求結束時 rollback 到 savepoint（與正式 `get_db` 的 close 同語意）。factory 只 flush 的資料要先 `db_session.commit()`（只釋放 savepoint，測試結束仍整筆 rollback）才能跨請求存在；兩次請求之間改的資料同樣要 commit。不可用 `lambda: db_session` 覆寫（DB 錯誤後 session 會停在 aborted）。
- 寫完測試後以突變自我檢查：把實作改壞一處，確認至少一條測試轉紅；轉不紅的斷言視為恆真，要改。

### 前端測試的已知陷阱（vitest + happy-dom）

- spec 讀 repo 內檔案時用 `resolve(dirname(fileURLToPath(import.meta.url)), '<相對路徑>')`；不要用 `new URL('<相對路徑>', import.meta.url)`，Vite 會把它改寫成 asset URL，`fileURLToPath` 會失敗。
- 判斷 axios 錯誤一律用 `axios.isAxiosError(e)`，不用 `instanceof AxiosError`：axios-mock-adapter 載入的是另一份 axios，`instanceof` 會判斷失敗。
- 只有 `.vue` SFC 會觸發 unplugin 的 Element Plus 自動 import；在 `.ts` 裡用 `defineComponent({ template })` 的字串 template 不會，測試 Element Plus 行為要用 SFC fixture。
- Element Plus 的 `el-select` 清除 icon 要對根元素 `.el-select` 觸發 `mouseenter` 才會出現，對內層元素觸發會讓「不可清除」的測試恆真。
- Element Plus 的 dialog 要等關閉動畫結束才 emit `update:modelValue`、才套用 `destroy-on-close`，happy-dom 不會觸發動畫結束事件。元件要自己在 `before-close` 中 emit 關閉，敏感內容（臨時密碼等）以 `v-if` 在關閉當下移除；斷言不得依賴動畫結束。
- `ElMessageBox` 要等下一個 tick 才渲染，斷言前先等待；已關閉的 message box 仍可能留在 DOM，找按鈕時取最後一個符合的元素。
- `useId()` 只在同一個 app 內唯一，比較兩個 id 要在同一次 mount 內放兩個元件，不能用兩次 mount 比對。
- happy-dom 不計算版面：版面相關的 decision（固定 footer、只有 body 捲動等）用 class / 結構斷言加 `?raw` 讀原始碼斷言樣式規則，並在回報說明；必要時用 Playwright 在真實 Chromium 量測。
- vitest 對 `*.css?raw` 回傳空字串（`.vue?raw` 正常），斷言 CSS 檔內容要用 `readFileSync` 搭配上面的 `resolve(dirname(fileURLToPath(import.meta.url)), ...)` 讀真實檔案。
- happy-dom 不實作「在欄位按 Enter 隱式送出表單」，在測試裡按 Enter 驗不出任何東西。瀏覽器的隱式送出是對預設送出鈕派發 click，所以測試改用對送出鈕呼叫 `element.click()` 模擬這條路徑。

## 3. 測試分層

| 層級 | marker | 可碰 DB | 預設執行 | 位置 |
|---|---|---|---|---|
| 後端單元 | `unit`（未標視同 unit） | 否（repository 以 fake 取代；不用 SQLite 冒充 Postgres） | 是 | `apps/api/tests/unit/` |
| 後端整合 | `integration` | 只能碰**本機** Postgres（`127.0.0.1:54342`）與本機 SeaweedFS S3（`127.0.0.1:54344`） | 否，需 `-m integration` | `apps/api/tests/integration/` |
| 前端元件/邏輯 | `vitest` | — | 是 | 與被測檔同目錄 `*.spec.ts` |
| 端對端 | `playwright` | 本地全套 | 否 | `apps/web/e2e/` |

- 後端 service 直接以 SQLAlchemy 存取 DB，主要以整合測試（本機 Postgres、以 `app_backend` 角色實際登入，禁止改用 owner 角色繞過授權）驗證；純函式與規則判斷用單元測試。
- 單元測試由 `pytest-socket` 擋外部網路（只放行 loopback 給 integration）。
- 整合測試每個測試在 transaction 內執行並 rollback；需要 commit 語意的（outbox、after-commit 通知）用獨立 fixture 清表。
- migration（含 grant）/ schema drift 的測試屬 integration。migration 測試以 owner 連線只限 DDL 探針、清表、查其他角色權限等明確場合，測資一律經 `app_backend` 寫入。
- R2 storage：單元測試以 botocore Stubber 或 FakeStorage 取代；與本機 SeaweedFS 的往返只在 storage 本身的整合測試驗證，其他 service / endpoint 測試一律用 FakeStorage。
- 前端 API 呼叫用 axios-mock-adapter，元件測試用真實 DOM 互動（`setValue`、`trigger('click')`）斷言 emit payload 與畫面文字。

## 4. 禁止全量執行

任何 lint / format / typecheck / test 指令都**必須指定路徑、檔案或 marker**：

```
just test apps/api/tests/unit/services/test_leave_service.py -k apply
just test-int apps/api/tests/integration/db/test_roles.py -k roles
just lint apps/api/app/services/leave_service.py
just web-test src/components/pickup/QueueCard.spec.ts
just web-lint src/components/pickup/QueueCard.vue
```

禁止：無參數 `pytest`、`pytest apps/api/tests/`、`ruff check .`、`pnpm lint`、無檔案的 `pnpm vitest run`。唯一例外 `just web-typecheck`（vue-tsc 只能全專案跑），只在 PR 前執行一次。全量執行只在 CI。

## 5. 本機環境

- 本機服務由 repo root 的 `compose.yaml` 提供：Postgres 17（`127.0.0.1:54342`，owner `postgres` / `postgres`）、SeaweedFS（S3 API `127.0.0.1:54344`，bucket `afterschool-local`，帳密 `afterschool` / `afterschool-local-secret`；只發佈 S3 port）。全部只綁 loopback，port 避開 5432x / 5433x。
- `just db-start` / `just db-stop` 啟停上述服務（停止保留 volume）；`just db-reset --yes` 重建本機 DB（drop / create `postgres` database → `alembic upgrade head` → 設定 `app_backend` 本機密碼 `app_backend_local`）；`just db-migrate` 只套用尚未套用的 revision。
- 測試用員工 / 家長帳號由 integration fixture 建立，不寫在任何 migration。
- docker 測試的陷阱：`docker run -v <來源>:<目標>` 的來源檔不存在時，Docker Desktop 會在主機建同名空目錄，容器改用映像內建設定而造成假綠；測試要先確認來源檔存在。容器之間互連用 user-defined network 與容器名稱，不要依賴 `host.docker.internal`（Linux CI 上連不到主機 loopback）。
- 會連 DB 的指令與腳本一律移除或拒絕 `PGHOST` / `PGHOSTADDR` / `PGSERVICE` / `PGSERVICEFILE`，否則 libpq 會依環境變數把 127.0.0.1 的 URL 導向別台主機。
