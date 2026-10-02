---
name: ui-design-preview
description: 後台與家長端每一個頁面與元件在實作前必經的 UI/UX 設計流程 —— 產出可互動的單檔設計稿、取得使用者核可、核可後才進入 TDD。當要實作、修改或討論 apps/web 的任何 view 或 UI 元件（FRONTEND-xxx / PARENT-xxx task）時使用；設計稿同時是實作階段的對照權威。
---

# UI Design Preview

- **設計圖**：`docs/mockups/*.html` 是每個頁面/元件外觀與互動的權威來源。實作照它做，完成後兩者必須一致。
- **設計流程**：出稿 → 使用者核可 → 才進 TDD。這是**硬性關卡**。

## 什麼時候一定要出稿

| `target_path` | 要不要稿 | 模板 |
|---|---|---|
| `apps/web/src/views/**`、`apps/web/src/components/**` | 要 | `references/preview-template.html`（後台，Element Plus） |
| `apps/web/src/parent/views/**`、`apps/web/src/parent/components/**` | 要 | `references/preview-template-parent.html`（家長端，手機框 + M3 風格） |
| `src/{api,composables,stores,constants,utils,router,shared}/**`、`src/parent/{api,stores,composables}/**` | 不要 | — |

稿的檔名：後台頁面 `page-<slug>.html`、後台元件 `component-<slug>.html`、家長端 `parent-page-<slug>.html` / `parent-component-<slug>.html`。一張頁面稿可以涵蓋該頁面內的多個元件 task（在 `preview-tasks` meta 列出全部 task id）。

## 流程

### 1. 建立可信現況（不可略過）

- 該 task 的 `description`、`risk_notes`、`open_design_questions`。
- **後端契約**：`docs/domain_spec.md` §2 與 `docs/tasks/backend/tasks.json` 中對應 endpoint 的 `description`。稿不可以畫出 API 給不出來的欄位。
- **權限**：`docs/domain_spec.md` §3 權限碼；後台頁面要畫出缺權限時的樣子（側欄隱藏 / 按鈕隱藏 / 唯讀）。
- **ivy 參考**：task 的 `source_ref` 指向的 ivy 元件，先看它現在長什麼樣，移植時保留好用的互動、去掉幼稚園 / 教師端特有的部分。
- **既有程式碼**：`apps/web/src/` 下相關的 router、constants、共用元件。

### 2. 出稿

1. 複製對應模板到 `docs/mockups/<slug>.html`。
2. 填 `<head>` 的四個 meta：`preview-status`（新稿一律 `draft`）、`preview-tasks`、`preview-route`、`preview-updated`。
3. 換掉 `PAGE CONTENT` 區塊；純元件稿把元件放進卡片逐一展示各種狀態。
4. 設計決定寫進 `decisions`，需要使用者選的寫進 `openQuestions`（每題兩個具體選項）。沒有真正歧義時 `openQuestions` 留空。
5. 更新 `docs/mockups/index.html`（全部稿與狀態的總覽頁；第一次執行時建立）。

**稿的內容要求**：

- 三態必畫：有資料 / 空清單（最終文案）/ 載入中；有錯誤態的加第四態。
- 權限差異必畫。
- 表格標出欄位、排序欄位、溢出提示欄位。
- Dialog / Drawer / BottomSheet 畫在所屬頁面稿裡，要能實際打開。
- 平板使用的頁面（接送 POS、作業進度看板、今日出勤）以 1024×768 為主要尺寸設計，觸控目標 ≥ 44px。
- 家長端以 390×844 手機尺寸設計。
- 假資料擬真但非真實個資（王小明、0912-000-123、某某國小 三年二班）。
- 顏色一律用 CSS 變數（後台 `--el-*`，家長端模板定義的 `--m3-*`），不要寫死 hex。

### 3. 送核可

一則訊息交付：稿的絕對路徑、涵蓋哪些 task、`decisions` 重點、待裁定問題（每題兩個選項，方便回「1A 2B」）。然後**停下來等回覆**，核可前不可動對應 task 的測試或實作。

### 4. 核可後

1. 依回覆修稿，`preview-status` 改 `approved`、`preview-updated` 改今天，更新 `index.html`。
2. 才開始 `docs/tasks/README.md` 的 TDD 流程。

```bash
grep -l "FRONTEND-023" docs/mockups/*.html | xargs grep -h 'name="preview-status"'
```

### 5. 實作時與實作後

- 稿是對照權威。實作發現稿有錯或做不到 → 回頭改稿，並在 `in_review` 回報說明。改動影響已核可的版面或流程時，`preview-status` 改回 `draft` 重送核可。
- 不要把稿的 CSS 複製進 `src/`：稿決定「長什麼樣、怎麼動」，不是「用哪幾行 CSS」。

## in-DOM template 的四個坑

模板用瀏覽器 in-DOM template（不經 build）：

1. **不可自閉合**：`<el-table-column ... />` 會吞掉後面的兄弟節點。一律寫完整結束標籤。
2. **icon 一律用 `icon-` 前綴**（`Filter` / `View` / `Menu` 等與原生標籤撞名）。
3. **屬性一律 kebab-case**：`:disable-transitions`、`v-model:current-page`。
4. **inline `<script>` 裡不可出現字面 `</script>`**，要拆開寫。

另外 `{{ }}` 在 `#app` 內會被求值，佔位文字不要用 `{{PAGE_TITLE}}`。

## 驗證稿真的能跑

靜態檢查全過不代表跑得動（`setup()` return 不存在的變數會讓整頁空白）。判定訊號四個都要是零：DOM 殘留的 `<el-*>` 標籤數、沒有 `<svg>` 的 `.el-icon` 數、未求值的 `{{ }}` 數、console error 數。

Playwright 擋 `file:`，要先起本機靜態伺服器：

```bash
cd docs/mockups && python3 -m http.server 8898 --bind 127.0.0.1 &
# browser_navigate http://127.0.0.1:8898/<slug>.html，檢查 console errors 為 0、截圖確認版面
# 驗完關 server、刪截圖與 .playwright-mcp/
```

多個 agent 平行做稿時瀏覽器是共用的，live 驗證要等全部稿產完再由一人序列跑。沒有瀏覽器工具時明講「未做 live 驗證」。

## 邊界

- 只管 `apps/web` 的 UI。
- 不要在稿裡發明後端沒有的功能；想加功能先改 task 的 `description` 與 `docs/domain_spec.md`，再反映到稿上。
