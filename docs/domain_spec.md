# 領域規格（功能 / 資料模型 / API / 頁面）

> 只記錄目前有效的最終決策。架構與技術棧見 `docs/architecture_decisions.md`。這份文件是各區域 `tasks.json` 的共同依據：欄位名、狀態值、權限碼、事件名以這裡為準，實作中若發現要改，先改這份文件再改 task。
> ivy 來源路徑縮寫：`BE:` = `/Users/user/personal_project/ivy/ivyManageSystem-backend/`，`FE:` = `/Users/user/personal_project/ivy/ivyManageSystem-frontend/`。

## 0. 名詞

| 名詞 | 意義 |
|---|---|
| 員工（staff） | 使用管理後台的人，包含管理員、主任、行政、課輔老師；權限由角色決定 |
| 家長（parent） | 透過 LINE LIFF 登入、綁定一或多位學生的帳號 |
| 監護人（guardian） | 學生的聯絡人紀錄；可對應（綁定）到一個家長帳號，也可以只是聯絡資料 |
| 班級（class） | 安親班內部的班，例如「低年級 A 班」；與學生就讀國小的班級（school_class 文字欄位）不同 |
| 營業日（service date） | 以 `Asia/Taipei` 判斷的日期 |

## 1. 模組與資料模型

所有表共用欄位：`id uuid pk`、`created_at`、`updated_at`（trigger 維護）。下列只寫業務欄位。`enum` 一律用 PostgreSQL `text + CHECK`（方便擴充），值域即下列括號內容。

### M1 帳號與權限

**staff_users**：`username`（unique，小寫）、`password_hash`（argon2）、`display_name`、`phone`、`email`、`role_id → roles`、`extra_permissions text[]`、`revoked_permissions text[]`、`is_active bool`、`token_version int`（改密碼/停用時 +1 使既有 token 失效）、`last_login_at`、`must_change_password bool`。

**roles**：`code`（unique）、`name`、`description`、`permissions text[]`、`is_system bool`（系統角色不可刪，`admin` 權限不可改）。預設 seed：`admin`（全部權限）、`director`、`clerk`、`tutor`，各自預設權限見 §3。

**refresh_tokens**：`subject_type`（`staff` / `parent`）、`subject_id`、`family_id`、`token_hash`、`expires_at`、`revoked_at`、`replaced_by`。refresh 輪替 + 偵測重用即撤銷整個 family（移植 `BE:utils/auth.py`）。

**audit_logs**：`actor_type`（`staff` / `parent` / `system` / `device`）、`actor_id`、`action`（例如 `exam_score.update`）、`entity_type`、`entity_id`、`before jsonb`、`after jsonb`、`ip`、`user_agent`。記錄：權限/角色/帳號異動、已發布成績修改、出勤手動改判、接送強制完成、系統設定修改、學生 Excel 匯入、學年升級、學生敏感欄位修改。

### M2 系統設定與參考資料

**system_settings**：`key`（unique）、`value jsonb`、`is_secret bool`、`updated_by`。key 由 `app/core/settings_registry.py` 註冊（key、Pydantic schema、預設值、是否 secret、顯示分組）。首批 key：

| key | 內容 |
|---|---|
| `org.profile` | 安親班名稱、地址、電話、Logo URL |
| `org.service_hours` | 每週營業時段（週一~週五 開始/結束、週六是否營業） |
| `pickup.window` | 可發起接送的時段、`我要來接` 可選的最晚時間、接送請求自動過期分鐘數 |
| `homework.defaults` | 未設定預計完成時間時是否自動回覆、預設提示文案 |
| `notification.toggles` | 各事件是否啟用（全域開關） |
| `line.liff` | LIFF ID 與 LINE Login channel ID（家長端登入、後端驗證 id_token 用） |
| `line.messaging` | channel access token、channel secret（secret，加密） |

**subjects**：`name`、`sort_order`、`is_active`。預設 seed：國語、數學、英語、自然、社會。
**exam_types**：`name`、`sort_order`、`is_active`。預設 seed：段考、小考、複習考。
**schools**（合作 / 學生就讀國小）：`name`、`short_name`、`is_active`。
**closed_days**（安親班休息日）：`date`（unique）、`reason`。出勤每日初始化跳過這些日期。

### M3 學生 / 班級 / 家長

**classes**：`name`、`grade_levels int[]`（1~6，可混齡）、`academic_year int`（民國學年度）、`sort_order`、`archived_at`。
**class_staff**：`class_id`、`staff_user_id`、`role`（`lead` / `assistant`）。僅作為後台篩選「我的班」與通知收件人，不是教師端。

**students**：`student_no`（unique，人工可編）、`name`、`gender`（`male` / `female` / `other`）、`birthday`、`grade_level int`（1~6）、`school_id → schools`、`school_class text`（例如「三年二班」）、`class_id → classes`、`status`（`active` / `suspended` / `withdrawn`）、`enrolled_on`、`withdrawn_on`、`photo_path`、`id_number_enc bytea`、`id_number_hmac text`（查重用，not null 時 unique）、`health_note_enc bytea`、`note`、`archived_at`。敏感欄位以應用層 AES-256-GCM 加密，HMAC 與加密金鑰皆由 `APP_SECRET_KEY` 衍生。
- 每年 8 月的升級（grade_level +1、六年級轉 withdrawn）由後台「學年升級」功能批次處理，不自動執行；升級時保留原安親班班級，由員工手動調班。
- 身分證字號與健康備註只有持 `students:sensitive` 的員工能檢視與寫入。

**parent_accounts**：`line_user_id`（unique）、`display_name`、`picture_url`、`phone`、`status`（`active` / `disabled`）、`token_version`、`last_login_at`。
**guardians**：`student_id`、`parent_account_id`（nullable，綁定後填入）、`name`、`relation`（`father` / `mother` / `grandparent` / `other`）、`phone`、`is_primary bool`、`can_pickup bool`、`receives_notifications bool`、`archived_at`。同一學生只能有一位 `is_primary`。
**parent_binding_codes**：`guardian_id`、`code_hash`、`expires_at`（預設 7 天）、`used_at`、`created_by`。綁定碼 8 碼英數，產生後只顯示一次（移植 `BE:models/parent_binding.py`）。

家長可見範圍：`guardians.parent_account_id = 自己` 且 guardian / student 未封存的學生（移植 `BE:api/parent_portal/_shared.py::_get_parent_student_ids` / `_assert_student_owned`）。

### M4 出勤

**student_attendances**：`student_id`、`service_date`、`status`（`expected` 預計到班 / `present` 已到班 / `left` 已離班 / `absent` 缺席 / `leave` 請假）、`check_in_at`、`check_in_source`（`manual` / `nfc`）、`check_out_at`、`check_out_source`（`manual` / `pickup` / `nfc`）、`leave_id → student_leaves`、`note`、`updated_by`。unique(`student_id`, `service_date`)。

狀態轉換：
```
expected ──到班──▶ present ──離班/接送完成──▶ left
   │                  │
   ├──標缺席──▶ absent  └──（誤刷可由有權限者改回，寫 audit）
   └──請假生效──▶ leave ──請假取消──▶ expected
```
- 每日初始化：營業日（非 `closed_days`、在 `org.service_hours` 內）為所有 `active` 學生建立 `expected` 紀錄；當日已有請假者直接建 `leave`。冪等。
- 到班時通知家長（`attendance.checked_in`）；離班時通知家長（`attendance.checked_out`）。
- 接送完成（M7）自動把出勤改為 `left`、`check_out_source = pickup`。

### M5 請假

**student_leaves**：`student_id`、`leave_type`（`sick` / `personal` / `other`）、`start_date`、`end_date`、`reason`、`status`（`active` / `cancelled`）、`created_by_type`（`parent` / `staff`）、`created_by_id`、`cancelled_at`、`cancelled_by_type`、`cancelled_by_id`。
**student_leave_attachments**：`leave_id`、`storage_path`、`mime_type`、`size_bytes`。檔案存 Supabase Storage 私有 bucket `leave-attachments`，後端簽發短效 URL。

- 同一學生的 `active` 請假期間不可重疊（DB exclusion constraint，API 回 409 `leave_overlap`），同時防止重複送出。
- 家長送出即生效（沿用 ivy），同時把期間內的出勤改 `leave`（移植 `BE:services/student_leave_service.py::apply_attendance_for_leave` / `revert_attendance_for_leave`）。員工可在後台代登記與取消。
- 已開始的請假日（今天之前）家長不可取消，員工可以。
- 建立/取消時通知班級負責員工（in_app + ws，事件 `leave.created` / `leave.cancelled`）。

### M6 作業進度

**homework_items**：`student_id`、`service_date`、`subject_id`（nullable）、`title`（例如「數學習作 p.12-13」）、`status`（`todo` / `doing` / `correcting` 訂正中 / `done`）、`sort_order`、`updated_by`。
**homework_daily_progress**：`student_id`、`service_date`、`overall_status`（`not_started` / `in_progress` / `done`）、`ready_eta`（time，預計可接送時間）、`eta_updated_by`、`eta_updated_at`、`note`（給家長看的說明）。unique(`student_id`, `service_date`)。
- `overall_status` 由 items 推導：無 item 或全部 `todo` → `not_started`；全部 `done` → `done`；其餘 `in_progress`。員工也可以直接把整體標 `done`（例如當天無作業）。
- 員工可對整班批次新增同一份作業項目（例如全班「國語第 5 課生字」）。
- 整體轉為 `done` 時通知家長（`homework.done`，文案「作業已完成，可以來接送了」）；`ready_eta` 被設定或變更時通知家長（`homework.eta_updated`）。
- 家長端可看當日每一項作業的狀態、整體狀態、預計可接送時間與說明。

### M7 接送與家長等待通知

**pickup_requests**（移植 `BE:models/dismissal.py::StudentDismissalCall`）：`student_id`、`service_date`、`source`（`parent` / `staff` / `proxy`）、`requested_by_type`、`requested_by_id`、`expected_arrival_at`（家長預計抵達時間，可空）、`status`（`pending` / `acknowledged` / `arrived` / `completed` / `cancelled` / `expired`）、`homework_status_at_request`、`reply_ready_eta`（回覆給家長的預計可接送時間）、`reply_message`、`reply_source`（`auto` / `staff`）、`replied_at`、`replied_by`、`arrived_at`、`completed_at`、`completed_by`、`picked_up_by_guardian_id`、`picked_up_by_authorization_id`、`completion_method`（`guardian` / `code` / `visual_match` / `override`）、`cancelled_at`、`cancel_reason`。同一學生同一天只能有一筆非終態請求（partial unique index）。

**pickup_persons**（常用接送人，移植 `BE:models/pickup.py::StudentPickupPerson`）：`student_id`、`name`、`relation`、`phone`、`photo_path`、`created_by_parent_id`、`archived_at`。
**pickup_authorizations**（單日代理接送，移植 `BE:models/pickup.py::PickupAuthorization`）：`student_id`、`service_date`、`pickup_person_id`（nullable）、`proxy_name`、`proxy_phone`、`code_hash`、`code_last4`、`code_attempts`、`code_locked_at`（接送碼連錯 5 次鎖定、不自動解鎖，需 `pickup:override` 員工處理）、`status`（`active` / `completed` / `cancelled`）、`verified_at`、`verified_by`、`verification_method`（`code` / `visual_match` / `override`）、`created_by_parent_id`。

流程（家長發起 + 員工主動更新兩者都要）：
1. 員工在「作業進度看板」持續更新 items 與 `ready_eta`（M6），家長隨時可在家長端看到。
2. 家長在家長端按「我要來接」（可選預計抵達時間）→ 建立 `pickup_request(pending)`，系統**立即自動回覆**：
   - 作業整體 `done` → 回覆「作業已完成，可以接送」，`reply_source = auto`。
   - 未完成且有 `ready_eta` → 回覆「預計 HH:MM 可接送」+ 員工說明，`reply_source = auto`。
   - 未完成且沒有 `ready_eta` → 回覆「已通知老師，稍後回覆預計時間」，並通知員工（ws + in_app）待回覆。
3. 員工在接送佇列看到請求，可**覆寫回覆**（設定/修改 `reply_ready_eta` 與訊息，`reply_source = staff`），按「確認」→ `acknowledged`，家長收到通知。
4. 家長抵達按「我到了」→ `arrived`（員工端提示音，移植 `useDismissalChime`）。
5. 員工交付學生 → `completed`（記錄由哪位監護人或哪張代理授權接走），出勤自動 `left`，通知家長。代理接送需核對接送碼或照片比對，或由有權限者強制完成（寫 audit）。
6. 家長可在 `completed` 前取消；超過 `pickup.window` 設定的分鐘數未完成自動 `expired`（背景工作）。
7. 員工也可替家長建立請求（`source = staff`，例如家長來電）。

即時性：接送佇列與作業進度看板透過 WebSocket 推送變更（頻道 `pickup`、`homework`），斷線時前端以輪詢補齊。

### M8 考試成績

**exams**：`name`（例如「第一次段考」）、`exam_type_id`、`exam_date`、`grade_level int`（nullable）、`class_id`（nullable；兩者至少一個，決定應考學生名單）、`status`（`draft` / `published`）、`published_at`、`published_by`、`note`。
**exam_subjects**：`exam_id`、`subject_id`、`full_score numeric`（預設 100）、`sort_order`。
**exam_scores**：`exam_id`、`student_id`、`subject_id`、`score numeric`（nullable，0 ≤ score ≤ full_score）、`is_absent bool`、`note`、`updated_by`。unique(`exam_id`, `student_id`, `subject_id`)。

- 後台以「學生 × 科目」格狀表格輸入，逐格自動儲存（批次 upsert）。
- 發布後家長端才看得到，並通知家長（`exam.published`）。發布後修改分數寫 audit，且可選擇是否重新通知。
- 科目、考試類型由後台自訂（M2）。後台可看單次考試各科平均與個人歷次成績列表（不做排名）。

### M9 通知

移植 `BE:services/notification/`（`dispatch.py::enqueue`、`channel_matrix.py`、`_channels/line.py`、`_channels/ws.py`、`outbox_sweeper.py`、`retry_scheduler.py`）。

**notifications**（站內收件匣）：`recipient_type`（`staff` / `parent`）、`recipient_id`、`event`、`title`、`body`、`payload jsonb`、`read_at`。
**notification_outbox**：`notification_id`、`channel`（只有 `line`；ws 不進 outbox，commit 後直接廣播、盡力而為，前端以輪詢補齊）、`status`（`pending` / `sent` / `failed` / `dead`）、`attempts`、`next_attempt_at`、`last_error`。DB transaction commit 後才派送，失敗指數退避，超過次數轉 `dead`。
**notification_preferences**：`parent_account_id`、`event`、`line_enabled bool`。in_app 一律開啟。

事件（`app/notifications/events.py`），收件人與預設頻道：

| event | 收件人 | 頻道 |
|---|---|---|
| `attendance.checked_in` | 學生的家長 | in_app, line |
| `attendance.checked_out` | 學生的家長 | in_app, line |
| `leave.created` / `leave.cancelled` | 班級負責員工 + 有 `leaves:read` 的員工 | in_app, ws |
| `homework.eta_updated` | 學生的家長 | in_app, line |
| `homework.done` | 學生的家長 | in_app, line |
| `pickup.requested` | 有 `pickup:operate` 的員工 | in_app, ws |
| `pickup.replied` | 發起請求的家長 | in_app, line |
| `pickup.arrived` | 有 `pickup:operate` 的員工 | ws |
| `pickup.completed` | 學生的家長 | in_app, line |
| `pickup.cancelled` | 家長取消 → 有 `pickup:operate` 的員工；員工取消 → 發起的家長 | 員工 in_app, ws；家長 in_app, line |
| `exam.published` | 應考學生的家長 | in_app, line |
| `binding.completed` | 綁定的家長 | in_app |

家長收件人 = 該學生 `receives_notifications = true` 且已綁定的 guardian 帳號（移植 `BE:services/notification/parent_recipients.py`）。

### M10 NFC 打卡（blocked）

**devices**：`name`、`device_key_hash`、`location`、`is_active`、`last_seen_at`。
**nfc_cards**：`card_uid`（unique）、`student_id`、`status`（`active` / `lost` / `revoked`）、`issued_at`。
API `POST /api/device/punch`（裝置金鑰認證，卡號 → 學生 → 到班/離班判斷，冪等防重刷）。機型、通訊方式、離線佇列、刷卡判斷規則（同一天第一次到班、第二次離班？）都尚未決定，**全部 task 標 `blocked`**，`open_design_questions` 寫明待決事項。

### M11 儀表板

後台首頁：今日應到 / 已到 / 未到 / 請假 / 缺席人數，待回覆接送請求數，作業完成率，近期請假清單。單一 endpoint 聚合。

## 2. API 介面

路徑前綴 `/api`。所有回應錯誤格式：`{"error": {"code": "...", "message": "...", "details": ...}}`。列表分頁：`?page=&page_size=`，回 `{"items": [...], "total": n}`。

### 健康檢查
- `GET /api/health`：不需登入，回 `{"status": "ok", "app_name": ..., "db": "ok"}`（DB 連不上回 503）。供 Railway healthcheck 與部署後 smoke 測試使用。

### 認證
| Method | Path | 說明 |
|---|---|---|
| POST | `/api/admin/auth/login` | 員工帳密登入，設 access/refresh cookie |
| POST | `/api/admin/auth/refresh` | 輪替 refresh |
| POST | `/api/admin/auth/logout` | 撤銷 family、清 cookie |
| GET | `/api/admin/auth/me` | 目前員工 + 有效權限碼 |
| POST | `/api/admin/auth/change-password` | 改密碼（token_version +1） |
| POST | `/api/parent/auth/liff-login` | LIFF id_token 登入（未綁定時回 `needs_binding`） |
| POST | `/api/parent/auth/bind` | 綁定碼綁定（首次 / 加綁其他小孩） |
| POST | `/api/parent/auth/refresh` / `logout` | 同上 |
| GET | `/api/parent/me` | 家長資料 + 已綁定小孩清單 |

### 後台（`/api/admin`，皆需員工登入 + 權限碼）
- 帳號：`GET/POST /staff-users`、`GET/PATCH /staff-users/{id}`、`POST /staff-users/{id}/reset-password`、`POST /staff-users/{id}/deactivate`、`POST /staff-users/{id}/activate`（重新啟用，產生臨時密碼並要求改密碼）
- 角色：`GET/POST /roles`、`PATCH/DELETE /roles/{id}`、`GET /permissions`（權限碼目錄，含分組與說明）
- 設定：`GET /settings`、`PUT /settings/{key}`；`GET/POST/PATCH/DELETE /subjects`、`/exam-types`、`/schools`、`/closed-days`
- 稽核：`GET /audit-logs`
- 班級：`GET/POST /classes`、`GET/PATCH /classes/{id}`、`POST /classes/{id}/archive`、`PUT /classes/{id}/staff`
- 學生：`GET/POST /students`、`GET/PATCH /students/{id}`、`POST /students/{id}/archive`、`POST /students/{id}/photo`、`POST /students/promote-grade`（學年升級，預覽 + 執行兩段）、`POST /students/import`（Excel 匯入）
- 監護人：`GET/POST /students/{id}/guardians`、`PATCH/DELETE /guardians/{id}`、`POST /guardians/{id}/binding-code`、`POST /guardians/{id}/unbind`
- 出勤：`GET /attendance/daily?date=&class_id=`、`POST /attendance/{student_id}/check-in`、`POST /attendance/{student_id}/check-out`、`POST /attendance/batch-check-in`、`POST /attendance/{student_id}/mark-absent`、`PATCH /attendance/{id}`（改判，寫 audit）、`GET /attendance/monthly?month=&class_id=`、`GET /attendance/monthly/export`
- 請假：`GET /leaves`、`POST /leaves`（代登記）、`POST /leaves/{id}/cancel`、`GET /leaves/{id}/attachments/{aid}`（簽發 URL）
- 作業：`GET /homework/board?date=&class_id=`、`POST /homework/items`（單一學生）、`POST /homework/items/batch`（整班）、`PATCH /homework/items/{id}`、`DELETE /homework/items/{id}`、`PUT /homework/progress/{student_id}`（overall / ready_eta / note）
- 接送：`GET /pickup/queue?date=`、`POST /pickup/requests`（員工代建）、`POST /pickup/requests/{id}/reply`、`POST /pickup/requests/{id}/acknowledge`、`POST /pickup/requests/{id}/complete`、`POST /pickup/requests/{id}/cancel`、`GET /pickup/roster?date=&class_id=`（POS 學生卡狀態，移植 `dismissal_pos.get_pos_status`）、`GET /pickup/authorizations?date=`、`POST /pickup/authorizations/{id}/verify`、`/confirm-visual-match`、`/override-complete`
- 成績：`GET/POST /exams`、`GET/PATCH/DELETE /exams/{id}`、`PUT /exams/{id}/subjects`、`GET /exams/{id}/scores`（格狀資料）、`PUT /exams/{id}/scores`（批次 upsert）、`POST /exams/{id}/publish`、`POST /exams/{id}/unpublish`、`GET /exams/{id}/summary`（各科平均）、`GET /students/{id}/exam-history`
- 通知：`GET /notifications`、`POST /notifications/{id}/read`、`POST /notifications/read-all`
- 儀表板：`GET /dashboard/today`

### 家長端（`/api/parent`，需家長登入，所有 `student_id` 參數都過 `assert_parent_owns_student`）
- `GET /children`、`GET /children/{id}`
- 出勤：`GET /children/{id}/attendance?month=`
- 請假：`GET /children/{id}/leaves`、`POST /leaves`、`POST /leaves/{id}/cancel`、`POST /leaves/{id}/attachments`
- 作業：`GET /children/{id}/homework?date=`（items + overall + ready_eta + note）
- 接送：`POST /pickup/requests`、`GET /pickup/requests/today`、`POST /pickup/requests/{id}/arrived`、`POST /pickup/requests/{id}/cancel`
- 接送人：`GET/POST /children/{id}/pickup-persons`、`DELETE /pickup-persons/{id}`；代理：`GET/POST /children/{id}/pickup-authorizations`、`POST /pickup-authorizations/{id}/cancel`
- 成績：`GET /children/{id}/exams`、`GET /children/{id}/exams/{exam_id}`
- 通知：`GET /notifications`、`POST /notifications/{id}/read`、`POST /notifications/read-all`、`GET/PUT /notification-preferences`
- 公開設定：`GET /config`（LIFF ID、安親班名稱/Logo，不需登入）

### WebSocket
- `/api/ws/admin`（cookie 認證，員工）：訂閱頻道 `pickup`、`homework`、`attendance`、`notifications`
- `/api/ws/parent`（cookie 認證，家長）：只收自己小孩相關的 `homework`、`pickup`、`notifications` 事件

### 打卡機（blocked）
- `POST /api/device/punch`、`GET /api/device/roster`

## 3. 權限碼

| 權限碼 | 說明 | 預設授予 |
|---|---|---|
| `dashboard:read` | 首頁儀表板 | director, clerk, tutor |
| `staff:read` / `staff:write` | 員工帳號 | director / — |
| `roles:read` / `roles:write` | 角色與權限 | director / — |
| `settings:read` / `settings:write` | 系統設定與參考資料 | director, clerk / director |
| `audit:read` | 稽核紀錄 | director |
| `classes:read` / `classes:write` | 班級 | 全部員工 / director, clerk |
| `students:read` / `students:write` | 學生 | 全部員工 / director, clerk |
| `students:sensitive` | 查看身分證、健康備註 | director |
| `guardians:write` | 監護人與綁定碼 | director, clerk |
| `attendance:read` / `attendance:operate` | 查出勤 / 到班離班登記 | 全部員工 |
| `attendance:amend` | 改判已登記的出勤（寫 audit） | director |
| `leaves:read` / `leaves:write` | 查請假 / 代登記與取消 | 全部員工 / director, clerk |
| `homework:read` / `homework:write` | 作業進度 | 全部員工 |
| `pickup:read` / `pickup:operate` | 接送佇列 / 回覆、確認、完成 | 全部員工 |
| `pickup:override` | 代理接送強制完成 | director |
| `exams:read` / `exams:write` / `exams:publish` | 成績 | 全部員工 / director, clerk, tutor / director, clerk |

NFC 管理權限碼 `nfc:manage`（預設只有 admin）在 NFC 解除 blocked 時才加入 `Permission` enum。

`admin` 角色在 DB 中的 `permissions` 存為 `{*}`，代表全部權限碼（含未來新增）；後端計算有效權限時把 `*` 展開成 `Permission` enum 的全部值。

## 4. 前端頁面

### 後台（`apps/web/src/views/`）
| 路由 | 頁面 | 權限 | ivy 參考 |
|---|---|---|---|
| `/login` | 登入 | — | `FE:src/views/LoginView.vue` |
| `/change-password` | 改密碼 | 登入 | `FE:src/views/ChangePasswordView.vue` |
| `/` | 今日儀表板 | `dashboard:read` | — |
| `/students` | 學生工作台（列表 + 詳情面板 + 監護人 + 綁定碼） | `students:read` | `FE:src/views/StudentWorkbenchView.vue`、`components/student/*`、`GuardianManager` |
| `/classes` | 班級管理 | `classes:read` | `FE:src/views/ClassroomView.vue` |
| `/attendance` | 今日出勤（點名/到班/離班） | `attendance:read` | `FE:src/views/StudentAttendanceView.vue`、`views/portal/components/studentAttendance/*` |
| `/attendance/monthly` | 月出勤報表 + 匯出 | `attendance:read` | `FE:src/views/StudentAttendanceView.vue` |
| `/leaves` | 請假列表 + 代登記 | `leaves:read` | `FE:src/views/StudentLeavesListView.vue` |
| `/homework` | 作業進度看板（平板友善，逐生卡片） | `homework:read` | 新做 |
| `/pickup` | 接送 POS（班級欄 / 學生卡 / 佇列 + 回覆預計時間） | `pickup:read` | `FE:src/views/DismissalQueueView.vue`、`components/dismissal/pos/*` |
| `/pickup/authorizations` | 代理接送核驗 | `pickup:read` | `FE:src/views/PickupAuthorizationsView.vue` |
| `/exams` | 考試列表 | `exams:read` | 新做 |
| `/exams/:id` | 成績輸入格 + 發布 + 各科平均 | `exams:read` | 新做 |
| `/settings/system` | 系統設定（依 registry 分組渲染表單） | `settings:read` | `FE:src/components/settings/SettingsLineTab.vue` |
| `/settings/reference` | 科目 / 考試類型 / 國小 / 休息日 | `settings:read` | — |
| `/settings/accounts` | 員工帳號 | `staff:read` | `FE:src/views/settings/SettingsAccountsView.vue` |
| `/settings/roles` | 角色權限 | `roles:read` | `FE:src/views/settings/SettingsRolesView.vue`、`RoleCardsGrid`、`PermissionPicker` |
| `/settings/audit` | 稽核紀錄 | `audit:read` | — |
| `/nfc/*` | 裝置與卡片管理（blocked） | — | — |

共用：`AdminLayout`、`AdminSidebar`、`AdminHeader`、`NotificationBell`、`PageHeader`、`FormDialog`、`AdminListToolbar`、`EmptyState`、`StatCard`（移植 `FE:src/components/common/*`、`components/layout/*`）。

### 家長端（`apps/web/src/parent/views/`）
| 路由 | 頁面 | ivy 參考 |
|---|---|---|
| `/login` | LIFF 登入 | `FE:src/parent/views/LoginView.vue` |
| `/bind` | 綁定碼綁定 / 加綁 | `FE:src/parent/views/BindView.vue`、`BindAdditionalView.vue` |
| `/` | 首頁：小孩切換 + 今日狀態卡（出勤、作業進度、預計可接送時間、接送按鈕） | `FE:src/parent/views/ChildHubView.vue` |
| `/homework` | 今日作業明細 | 新做 |
| `/pickup` | 我要來接 / 我到了 / 回覆訊息 / 取消 | `FE:src/parent/views/PickupNoticeView.vue` |
| `/pickup/proxy` | 常用接送人 + 代理接送碼 | `FE:src/parent/views/PickupView.vue`、`PickupCreateView.vue` |
| `/attendance` | 月出勤日曆 | `FE:src/parent/views/AttendanceView.vue` |
| `/leaves` | 請假申請 / 紀錄 / 取消 | `FE:src/parent/views/LeavesView.vue`、`components/leaves/*` |
| `/exams` | 成績列表 + 單次明細 | 新做 |
| `/notifications` | 通知收件匣 + 偏好設定 | `FE:src/parent/views/NotificationPrefsView.vue` |

## 5. Open questions（尚未決定，已記錄在對應 task 的 `open_design_questions`）

- NFC 機型、通訊協定、離線佇列、刷卡判斷規則（M10 全部 blocked）。
- 是否需要大螢幕叫號畫面（目前只做 POS 平板頁）。
- Railway 是否會開多實例（決定是否實作 Redis broadcaster）。
- 是否需要從既有系統匯入學生資料（目前只做 Excel 匯入）。
- 個資保存期限與退班學生資料刪除政策。
