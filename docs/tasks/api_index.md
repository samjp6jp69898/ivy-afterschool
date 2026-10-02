# API 索引（前端拆 task 依據）

> 由 `docs/tasks/backend/tasks.json` 整理，欄位與錯誤碼以各 task 的 `description` 為準；本檔只列前端串接需要的摘要。
> 規格權威：`docs/domain_spec.md` §2。異動 endpoint 時同步更新本檔。

## 共通約定

- 路徑前綴 `/api`；後台 `/api/admin/*`（員工 cookie + 權限碼），家長端 `/api/parent/*`（家長 cookie）。
- 認證：httpOnly cookie（員工 `staff_access` / `staff_refresh`，家長 `parent_access` / `parent_refresh`，綁定流程 `parent_bind`）。前端不碰 token；401 時呼叫對應 refresh 一次，仍 401 → 導向登入。
- 錯誤：`{"error": {"code": str, "message": 繁中, "details": any}}`。422 `validation_error` 的 details 為 `[{loc, msg, type}]`。後台缺權限 403 `permission_denied`（details.required）；強制改密碼期間 403 `password_change_required`。
- 列表：`?page=&page_size=`（page_size ≤ 200），回 `{items, total}`（下表記為 `Page[X]`）。
- 時間：時間點欄位（`*_at`）為 ISO8601 UTC，前端轉 Asia/Taipei 顯示；`HH:MM` 字串欄位（ready_eta、expected_arrival_at、reply_ready_eta）為台北當地時間；日期為 `YYYY-MM-DD`。
- 成績分數（Decimal）以 JSON number 輸出。
- 家長端所有帶 `student_id` 的路徑或 body：非自己的小孩與不存在一律回相同 404（例如 `student_not_found`），前端不需區分。
- 狀態碼慣例：建立回 201、刪除回 204（另有註明者除外）。

---

## 1. 健康檢查 / 公開

| task | method | path | 權限 | request → response |
|---|---|---|---|---|
| B021 | GET | `/api/health` | — | → `{status: "ok"\|"degraded", app_name, db: "ok"\|"error"}`（DB 失敗 503） |
| B125 | GET | `/api/parent/config` | 不需登入 | → `{liff_id, org_name, logo_url}` |

## 2. 後台認證（`/api/admin/auth`）

| task | method | path | 權限 | request → response |
|---|---|---|---|---|
| B042 | POST | `/auth/login` | — | `{username, password}` → `{user: StaffMe}` 並設 cookie；429 `too_many_attempts`（Retry-After） |
| B044 | POST | `/auth/refresh` | refresh cookie | → `{user: StaffMe}` |
| B046 | POST | `/auth/logout` | — | → `{message}` |
| B048 | GET | `/auth/me` | 登入 | → `StaffMe` |
| B050 | POST | `/auth/change-password` | 登入 | `{current_password, new_password}` → `{user: StaffMe}`；422 `weak_password`（details.reasons；規則：≥10 碼且含英文與數字） |

`StaffMe = {id, username, display_name, role: {id, code, name}, permissions: string[], must_change_password}`

## 3. 家長認證與個人資料（`/api/parent`）

| task | method | path | 權限 | request → response |
|---|---|---|---|---|
| B053 | POST | `/auth/liff-login` | — | `{id_token}` → `{status: "ok"\|"needs_binding", parent: ParentMe\|null, name_hint}` |
| B057 | POST | `/auth/bind` | 家長 cookie 或 bind cookie | `{code}` → `{parent: ParentMe}` |
| B059 | POST | `/auth/refresh` | refresh cookie | → `{parent: ParentMe}` |
| B061 | POST | `/auth/logout` | — | → `{message}` |
| B064 | GET | `/me` | 家長 | → `ParentMe` |
| B184 | GET | `/children` | 家長 | → `ChildSummary[]` |
| B185 | GET | `/children/{student_id}` | 家長 | → `ChildDetail` |

- `ParentMe = {id, display_name, picture_url, phone, children: ChildSummary[]}`
- `ChildSummary = {id, name, grade_level, class_name, school_name, photo_url, status}`
- `ChildDetail = ChildSummary + {school_class, enrolled_on, my_guardian: {relation, is_primary, can_pickup, receives_notifications}}`

## 4. 後台：帳號 / 角色 / 稽核 / 設定（`/api/admin`）

| task | method | path | 權限 | request → response |
|---|---|---|---|---|
| B081 | GET | `/roles` | roles:read 或 staff:read | → `Role[]` |
| B082 | POST | `/roles` | roles:write | `{code, name, description, permissions[]}` → 201 `Role` |
| B083 | PATCH | `/roles/{id}` | roles:write | `{name?, description?, permissions?}` → `Role` |
| B084 | DELETE | `/roles/{id}` | roles:write | → 204 |
| B085 | GET | `/permissions` | roles:read 或 staff:read | → `{groups: [{key, label, permissions: [{code, label}]}]}` |
| B093 | GET | `/staff-users?q&role_id&is_active` | staff:read | → `Page[StaffUser]` |
| B094 | POST | `/staff-users` | staff:write | `{username, display_name, phone?, email?, role_id, extra_permissions[], revoked_permissions[]}` → 201 `{user: StaffUser, temp_password}` |
| B095 | GET | `/staff-users/{id}` | staff:read | → `StaffUser` |
| B096 | PATCH | `/staff-users/{id}` | staff:write | `{display_name?, phone?, email?, role_id?, extra_permissions?, revoked_permissions?}` → `StaffUser` |
| B097 | POST | `/staff-users/{id}/reset-password` | staff:write | → `{temp_password}` |
| B098 | POST | `/staff-users/{id}/deactivate` | staff:write | → `StaffUser` |
| B522 | POST | `/staff-users/{id}/activate` | staff:write | → `{user: StaffUser, temp_password}`（啟用後 must_change_password=true；409 `staff_already_active`） |
| B105 | GET | `/audit-logs?action&action_prefix&entity_type&entity_id&actor_type&actor_id&date_from&date_to` | audit:read | → `Page[{id, created_at, actor_type, actor_id, actor_name, action, entity_type, entity_id, before, after, ip, user_agent}]` |
| B112 | GET | `/settings` | settings:read | → `{items: [{key, group, label, is_secret, value, json_schema, updated_at, updated_by_name}]}`（secret 欄位遮罩） |
| B113 | PUT | `/settings/{key}` | settings:write | `{value: {...}}` → `Setting`；422 `invalid_setting_value` |

- `Role = {id, code, name, description, is_system, permissions[], effective_permissions[], staff_count, created_at, updated_at}`
- `StaffUser = {id, username, display_name, phone, email, role: {id, code, name}, extra_permissions[], revoked_permissions[], effective_permissions[], is_active, must_change_password, last_login_at, created_at}`
- 設定 key：`org.profile`、`org.service_hours`、`pickup.window`、`homework.defaults`、`notification.toggles`、`line.liff`（`{liff_id, channel_id}`）、`line.messaging`（secret）。表單依 `json_schema` 渲染。

## 5. 後台：參考資料（`/api/admin/{resource}`，resource = `subjects` / `exam-types` / `schools` / `closed-days`）

| task | method | path | 權限 | request → response |
|---|---|---|---|---|
| B119 | GET | `/{resource}?active_only&date_from&date_to` | settings:read / students:read / exams:read / homework:read 任一 | → `Item[]` |
| B120 | POST | `/{resource}` | settings:write | create body → 201 `Item` |
| B121 | PATCH | `/{resource}/{id}` | settings:write | update body → `Item` |
| B122 | DELETE | `/{resource}/{id}` | settings:write | → `{deleted, deactivated}`（被引用時改為停用） |

- subjects / exam-types：`{id, name, sort_order, is_active}`；schools：`{id, name, short_name, is_active}`；closed-days：`{id, date, reason}`（date 不可改）。

## 6. 後台：班級 / 學生 / 監護人（`/api/admin`）

| task | method | path | 權限 | request → response |
|---|---|---|---|---|
| B141 | GET | `/classes?academic_year&include_archived&mine` | classes:read | → `Class[]` |
| B142 | POST | `/classes` | classes:write | `{name, grade_levels[], academic_year, sort_order}` → 201 `Class` |
| B143 | GET | `/classes/{id}` | classes:read | → `Class` |
| B144 | PATCH | `/classes/{id}` | classes:write | 部分欄位 → `Class` |
| B145 | POST | `/classes/{id}/archive` | classes:write | → `Class` |
| B146 | PUT | `/classes/{id}/staff` | classes:write | `{items: [{staff_user_id, role: lead\|assistant}]}` → `Class` |
| B159 | GET | `/students?q&class_id&grade_level&status&school_id&include_archived` | students:read | → `Page[StudentListItem]` |
| B160 | POST | `/students` | students:write（敏感欄位另需 students:sensitive） | `StudentCreate` → 201 `StudentDetail` |
| B161 | GET | `/students/{id}` | students:read | → `StudentDetail` |
| B162 | PATCH | `/students/{id}` | students:write（敏感欄位另需 students:sensitive） | 部分欄位（id_number / health_note 給 null = 清除）→ `StudentDetail` |
| B163 | POST | `/students/{id}/archive` | students:write | → `StudentDetail` |
| B164 | POST | `/students/{id}/photo` | students:write | multipart `file` → `{photo_url}` |
| B165 | POST | `/students/promote-grade` | students:write | `{from_academic_year, dry_run, expected_total?, withdrawn_on?}` → 預覽 `{from_academic_year, to_academic_year, promote[], graduate[], total, already_promoted}` 或結果 `{promoted, graduated}`（班級不變） |
| B166 | POST | `/students/import` | students:write | multipart `file`(xlsx)、`academic_year`、`dry_run` → 預覽 `{rows: [{row_number, display, errors[]}], total, valid, invalid}` 或結果 `{created, student_ids[]}` |
| B173 | GET | `/students/{id}/guardians` | students:read | → `Guardian[]` |
| B174 | POST | `/students/{id}/guardians` | guardians:write | `{name, relation, phone?, is_primary, can_pickup, receives_notifications}` → 201 `Guardian` |
| B175 | PATCH | `/guardians/{id}` | guardians:write | 部分欄位 → `Guardian` |
| B176 | DELETE | `/guardians/{id}` | guardians:write | → 204 |
| B177 | POST | `/guardians/{id}/binding-code` | guardians:write | → 201 `{guardian_id, code, expires_at}`（明碼只回一次） |
| B178 | POST | `/guardians/{id}/unbind` | guardians:write | → `Guardian` |

- `Class = {id, name, grade_levels[], academic_year, sort_order, archived_at, student_count, staff: [{staff_user_id, display_name, role}]}`
- `StudentCreate = {student_no, name, gender?, birthday?, grade_level, school_id?, school_class?, class_id?, status?, enrolled_on?, withdrawn_on?, note?, id_number?, health_note?}`
- `StudentListItem = {id, student_no, name, gender, grade_level, school: {id, name, short_name}|null, school_class, class: {id, name}|null, status, archived_at}`
- `StudentDetail = StudentListItem + {birthday, enrolled_on, withdrawn_on, note, photo_url, has_id_number, has_health_note, sensitive: {id_number, health_note}|null（需 students:sensitive）, guardians: Guardian[]}`
- `Guardian = {id, student_id, name, relation, phone, is_primary, can_pickup, receives_notifications, binding: {status: bound|code_issued|unbound, parent_display_name, code_expires_at}}`

## 7. 後台：出勤（`/api/admin/attendance`）

| task | method | path | 權限 | request → response |
|---|---|---|---|---|
| B315 | GET | `/attendance/daily?date&class_id&status` | attendance:read | → `{date, is_service_day, summary: {total, expected, present, left, absent, leave}, items: AttendanceRow[]}` |
| B316 | POST | `/attendance/{student_id}/check-in` | attendance:operate | `{note?}`（可省略 body）→ `AttendanceRow`；409 `already_checked_in` / `already_checked_out` / `student_on_leave` / `not_service_day` / `student_not_active` |
| B317 | POST | `/attendance/{student_id}/check-out` | attendance:operate | `{note?}` → `AttendanceRow`；409 `not_checked_in` / `already_checked_out` / `student_on_leave` |
| B318 | POST | `/attendance/batch-check-in` | attendance:operate | `{student_ids[] (1~200)}` → `{succeeded: AttendanceRow[], skipped: [{student_id, code, message}]}` |
| B319 | POST | `/attendance/{student_id}/mark-absent` | attendance:operate | `{note?}` → `AttendanceRow`（冪等）；409 `already_checked_in` / `student_on_leave` |
| B320 | PATCH | `/attendance/{attendance_id}` | attendance:amend | `{status?: expected\|present\|left\|absent, check_in_at?, check_out_at?, note?, reason}` → `AttendanceRow`；409 `attendance_managed_by_leave`；422 `check_in_required` / `check_out_required` / `invalid_times` / `time_not_on_service_date` / `no_changes` |
| B321 | GET | `/attendance/monthly?month=YYYY-MM&class_id` | attendance:read | → `{month, class_id, class_name, days: [{date, weekday, is_service_day}], students: [{student_id, student_no, name, class_name, statuses[], stats}], totals}` |
| B322 | GET | `/attendance/monthly/export?month&class_id` | attendance:read | → xlsx 檔（Content-Disposition 檔名） |

- `AttendanceRow = {id|null（營業日尚未建列的虛擬列）, student_id, student_no, student_name, grade_level, class_id, class_name, service_date, status: expected|present|left|absent|leave, check_in_at, check_in_source, check_out_at, check_out_source, leave: {id, leave_type, leave_type_label, start_date, end_date}|null, note, updated_at}`
- `stats = {service_days, attended, absent, leave, unrecorded}`；`statuses[i]` 與 `days[i]` 對齊，null = 非營業日或無紀錄。

## 8. 後台：請假（`/api/admin/leaves`）

| task | method | path | 權限 | request → response |
|---|---|---|---|---|
| B351 | GET | `/leaves?student_id&class_id&status&leave_type&created_by_type&date_from&date_to` | leaves:read | → `Page[Leave]` |
| B352 | POST | `/leaves` | leaves:write | `{student_id, leave_type: sick\|personal\|other, start_date, end_date, reason?}` → 201 `Leave`；409 `leave_overlap`（details: leave_id, start_date, end_date）/ `student_not_active`；422 `no_service_days_in_range` |
| B353 | POST | `/leaves/{id}/cancel` | leaves:write | `{scope?: "remaining"\|"all"}`（預設 remaining）→ `Leave`。remaining：未開始整筆 cancelled、已開始則 end_date 改為昨天（status 仍 active），今天起出勤回 expected；all：整筆取消。409 `leave_not_active` / `leave_already_ended`（remaining 且已全部過去） |
| B354 | GET | `/leaves/{id}/attachments/{attachment_id}` | leaves:read | → `{url, expires_in}`（短效，勿快取） |

- `Leave = {id, student: {id, student_no, name, class_name}, leave_type, leave_type_label, start_date, end_date, reason, status: active|cancelled, created_by_type: parent|staff, created_by_name, created_at, cancelled_at, cancelled_by_type, cancelled_by_name, attachments: [{id, mime_type, size_bytes, created_at}]}`

## 9. 後台：作業進度（`/api/admin/homework`）

| task | method | path | 權限 | request → response |
|---|---|---|---|---|
| B385 | GET | `/homework/board?date&class_id` | homework:read | → `{date, summary: {total, done, in_progress, not_started}, students: [{student_id, student_no, name, class_id, class_name, attendance_status, items: HomeworkItem[], progress: Progress}]}` |
| B386 | POST | `/homework/items` | homework:write | `{student_id, service_date?, subject_id?, title, status?, sort_order?}` → 201 `{item: HomeworkItem, progress: Progress}` |
| B387 | POST | `/homework/items/batch` | homework:write | `{class_id, service_date?, subject_id?, title, student_ids?}` → 201 `{created, items: HomeworkItem[]}`；422 `student_not_in_class` / `no_students` |
| B388 | PATCH | `/homework/items/{id}` | homework:write | `{title?, subject_id?, status?, sort_order?}` → `{item, progress}` |
| B389 | DELETE | `/homework/items/{id}` | homework:write | → 200 `{item: null, progress}` |
| B390 | PUT | `/homework/progress/{student_id}` | homework:write | `{service_date?, overall?: "done"\|"auto", ready_eta?: "HH:MM"\|null, note?: string\|null}` → `Progress` |

- `HomeworkItem = {id, student_id, service_date, subject_id, subject_name, title, status: todo|doing|correcting|done, sort_order, updated_at}`
- `Progress = {student_id, service_date, overall_status: not_started|in_progress|done, ready_eta: "HH:MM"|null, note, eta_updated_at, eta_updated_by_name}`
- service_date 範圍：今天 −30 ~ +7 天，否則 422 `invalid_service_date`。

## 10. 後台：接送（`/api/admin/pickup`）

| task | method | path | 權限 | request → response |
|---|---|---|---|---|
| B429 | GET | `/pickup/queue?date` | pickup:read | → `{date, open: PickupRequest[], closed: PickupRequest[], counts: {pending, acknowledged, arrived, needs_reply}}` |
| B430 | POST | `/pickup/requests` | pickup:operate | `{student_id, expected_arrival_at?: "HH:MM"}` → 201 `PickupRequest`（source=staff）；409 `pickup_request_exists` / `student_not_available` / `not_service_day` |
| B431 | POST | `/pickup/requests/{id}/reply` | pickup:operate | `{reply_ready_eta?: "HH:MM", reply_message?}`（至少一個）→ `PickupRequest` |
| B432 | POST | `/pickup/requests/{id}/acknowledge` | pickup:operate | → `PickupRequest`（pending → acknowledged） |
| B433 | POST | `/pickup/requests/{id}/complete` | pickup:operate（method=override 另需 pickup:override） | `{method: "guardian"\|"override", guardian_id?, note?}` → `PickupRequest`；422 `invalid_guardian`；409 `guardian_cannot_pickup` |
| B434 | POST | `/pickup/requests/{id}/cancel` | pickup:operate | `{reason?}` → `PickupRequest` |
| B435 | GET | `/pickup/roster?date&class_id` | pickup:read | → `{date, classes: [{class_id\|null, class_name, students: [{student_id, student_no, name, grade_level, attendance_status, check_in_at, check_out_at, leave_type, homework_status, ready_eta, open_request: {id, status, expected_arrival_at, needs_reply}\|null, active_authorization_count}]}]}` |
| B436 | GET | `/pickup/authorizations?date&status` | pickup:read | → `StaffAuthorization[]` |
| B437 | POST | `/pickup/authorizations/{id}/verify` | pickup:operate | `{code}` → `{authorization: StaffAuthorization, request: PickupRequest}`；400 `pickup_code_mismatch`（details.remaining_attempts）；409 `pickup_code_locked` / `authorization_not_active` / `authorization_not_today` |
| B438 | POST | `/pickup/authorizations/{id}/confirm-visual-match` | pickup:operate | `{note?}`（可省略）→ `{authorization, request}`。任何 active 授權皆可：有 photo_url 時顯示照片輔助比對，沒有照片時核對證件後確認；409 `pickup_code_locked` / `authorization_not_active` / `authorization_not_today` |
| B439 | POST | `/pickup/authorizations/{id}/override-complete` | pickup:override | `{note}` → `{authorization, request}`（可處理已鎖定的授權） |

- 共通錯誤：404 `pickup_request_not_found`、409 `invalid_pickup_status`（details.current_status）。
- `PickupRequest = {id, student: {id, student_no, name, grade_level, class_id, class_name}, service_date, source: parent|staff|proxy, requested_by_type, requested_by_name, expected_arrival_at, status: pending|acknowledged|arrived|completed|cancelled|expired, homework_status_at_request, current_homework_status, current_ready_eta, reply_ready_eta, reply_message, reply_source: auto|staff|null, replied_at, replied_by_name, needs_reply, arrived_at, completed_at, completed_by_name, completion_method: guardian|code|visual_match|override|null, picked_up_by_name, cancelled_at, cancel_reason, created_at}`
- `StaffAuthorization = {id, student: {...}, student_id, service_date, pickup_person_id, proxy_name, proxy_phone, code_last4, status: active|completed|cancelled, effective_status, verified_at, verification_method, created_at, photo_url, code_attempts, locked, verified_by_name}`

## 11. 後台：成績（`/api/admin/exams`）

| task | method | path | 權限 | request → response |
|---|---|---|---|---|
| B467 | GET | `/exams?q&status&exam_type_id&class_id&grade_level&date_from&date_to` | exams:read | → `Page[Exam]` |
| B468 | POST | `/exams` | exams:write | `{name, exam_type_id, exam_date, grade_level?, class_id?, note?}`（兩者至少一個）→ 201 `Exam`；422 `invalid_exam_type` / `invalid_class` / `grade_not_in_class` |
| B469 | GET | `/exams/{id}` | exams:read | → `Exam` |
| B470 | PATCH | `/exams/{id}` | exams:write | 部分欄位 → `Exam`；已發布只可改 name / exam_date / note，否則 409 `exam_published` |
| B471 | DELETE | `/exams/{id}` | exams:write | → 204（只限 draft） |
| B472 | PUT | `/exams/{id}/subjects` | exams:write | `{items: [{subject_id, full_score, sort_order}]}` → `Exam`；移除科目會刪除該科成績；409 `full_score_below_existing` / `exam_published` |
| B473 | GET | `/exams/{id}/scores` | exams:read | → `{exam: Exam, students: [{id, student_no, name, class_name, in_roster}], subjects: ExamSubject[], cells: ScoreCell[]}` |
| B474 | PUT | `/exams/{id}/scores` | exams:write | `{cells: [{student_id, subject_id, score\|null, is_absent, note?}] (1~2000), notify_parents}` → `{written, changed, renotified_students}`；422 `invalid_score_cells`（details: [{student_id, subject_id, code}]） |
| B475 | POST | `/exams/{id}/publish` | exams:publish | → `Exam`；422 `exam_has_no_subjects`；409 `exam_already_published` |
| B476 | POST | `/exams/{id}/unpublish` | exams:publish | → `Exam`；409 `exam_not_published` |
| B477 | GET | `/exams/{id}/summary` | exams:read | → `{exam_id, roster_count, subjects: [{subject_id, subject_name, full_score, scored_count, absent_count, missing_count, average, max, min}]}` |
| B478 | GET | `/students/{id}/exam-history` | exams:read | → `[{exam_id, exam_name, exam_type_name, exam_date, status, scores: [{subject_id, subject_name, full_score, score, is_absent}]}]` |

- `Exam = {id, name, exam_type: {id, name}, exam_date, grade_level, class: {id, name}|null, status: draft|published, published_at, published_by_name, note, subjects: ExamSubject[], roster_count, created_at}`
- `ExamSubject = {subject_id, subject_name, full_score, sort_order}`；`ScoreCell = {student_id, subject_id, score, is_absent, note, updated_at}`
- 逐格自動儲存：每次只送有變動的格子；已發布考試修改會寫稽核，`notify_parents=true` 時重新通知變動學生的家長。

## 12. 後台：通知 / 儀表板

| task | method | path | 權限 | request → response |
|---|---|---|---|---|
| B214 | GET | `/api/admin/notifications?unread_only` | 登入 | → `{items: Notification[], total, unread_count}` |
| B215 | POST | `/api/admin/notifications/{id}/read` | 登入 | → `Notification` |
| B216 | POST | `/api/admin/notifications/read-all` | 登入 | → `{updated}` |
| B492 | GET | `/api/admin/dashboard/today` | dashboard:read | → `{date, is_service_day, attendance: {expected_total, arrived, present, left, not_arrived, leave, absent}, pickup: {open, needs_reply, arrived, completed}, homework: {total, done, in_progress, not_started, completion_rate}, recent_leaves: [{id, student_id, student_name, class_name, leave_type_label, start_date, end_date, created_by_type, created_at}]}` |

- `Notification = {id, event, title, body, payload, read_at, created_at, deep_link}`（deep_link 為前端 hash 路由）。

## 13. 後台：NFC（blocked，路徑暫定、尚未實作）

權限 `nfc:manage`（預設只有 admin；NFC 解除 blocked 時才加入 Permission enum 與前端常數）。

| task | method | path | request → response |
|---|---|---|---|
| B510 | GET | `/api/admin/nfc/cards?student_id&status` | → `Page[{id, card_uid, student: {id, student_no, name}, status: active\|lost\|revoked, issued_at}]` |
| B511 | POST | `/api/admin/nfc/cards` | `{card_uid, student_id}` → 201 |
| B512 | PATCH | `/api/admin/nfc/cards/{id}` | `{status}` → card |
| B516 | GET | `/api/admin/nfc/devices` | → `[{id, name, location, is_active, last_seen_at}]` |
| B517 | POST | `/api/admin/nfc/devices` | `{name, location?}` → 201 `{device, device_key}`（金鑰只回一次） |
| B518 | PATCH | `/api/admin/nfc/devices/{id}` | `{name?, location?, is_active?}` → device |

打卡機端（非前端使用，X-Device-Key 認證）：B504 `POST /api/device/punch`、B506 `GET /api/device/roster`。

---

## 14. 家長端業務 API（`/api/parent`）

| task | method | path | request → response |
|---|---|---|---|
| B323 | GET | `/children/{id}/attendance?month=YYYY-MM` | → `{student_id, month, days: [{date, is_service_day, status, check_in_at, check_out_at, leave_type}], stats: {service_days, attended, absent, leave, unrecorded}}` |
| B355 | GET | `/children/{id}/leaves` | → `Page[ParentLeave]` |
| B356 | POST | `/leaves` | `{student_id, leave_type, start_date, end_date, reason?}` → 201 `ParentLeave`；422 `leave_date_out_of_window`（今天 −30 ~ +60 天）/ `no_service_days_in_range`；409 `leave_overlap` / `student_not_active` |
| B357 | POST | `/leaves/{id}/cancel` | → `ParentLeave`：未開始（含今天開始）整筆 cancelled；已開始則取消今天起的日子（end_date 改為昨天、status 仍 active）；409 `leave_already_ended` / `leave_not_active` |
| B358 | POST | `/leaves/{id}/attachments` | multipart `file`（jpg/png/webp/heic/pdf，≤10 MB，每筆最多 3 個）→ 201 `{id, mime_type, size_bytes, created_at, url}`；413 / 415 / 409 `attachment_limit_reached` |
| B391 | GET | `/children/{id}/homework?date` | → `{student_id, date, items: [{title, subject_name, status}], overall_status, ready_eta, note, updated_at}` |
| B440 | POST | `/pickup/requests` | `{student_id, expected_arrival_at?: "HH:MM"}` → 201 `ParentPickupRequest`（含自動回覆）；403 `pickup_not_allowed`；409 `pickup_request_exists` / `pickup_window_closed` / `student_not_available` / `not_service_day`；422 `expected_arrival_in_past` / `expected_arrival_too_late` |
| B441 | GET | `/pickup/requests/today` | → `ParentPickupRequest[]`（所有小孩今日，含終態） |
| B442 | POST | `/pickup/requests/{id}/arrived` | → `ParentPickupRequest`（我到了） |
| B443 | POST | `/pickup/requests/{id}/cancel` | `{reason?}` → `ParentPickupRequest` |
| B444 | GET | `/children/{id}/pickup-persons` | → `PickupPerson[]` |
| B445 | POST | `/children/{id}/pickup-persons` | multipart：`name`、`relation`、`phone`、`photo?` → 201 `PickupPerson`；409 `pickup_person_limit_reached`（上限 10） |
| B446 | DELETE | `/pickup-persons/{id}` | → 204 |
| B447 | GET | `/children/{id}/pickup-authorizations` | → `ParentAuthorization[]`（近 30 天起） |
| B448 | POST | `/children/{id}/pickup-authorizations` | `{service_date (今天~+14 天), pickup_person_id}` 或 `{service_date, proxy_name, proxy_phone}` → 201 `{authorization: ParentAuthorization, code}`（6 位接送碼只回一次）；409 `authorization_limit_reached`（同日 3 筆） |
| B449 | POST | `/pickup-authorizations/{id}/cancel` | → `ParentAuthorization`；409 `authorization_not_active` |
| B524 | POST | `/pickup-authorizations/{id}/regenerate-code` | → `{authorization: ParentAuthorization, code}`（新碼只回一次，舊碼立即失效、連錯次數與鎖定重設）；409 `authorization_not_active` / `authorization_expired` |
| B479 | GET | `/children/{id}/exams` | → `Page[{exam_id, name, exam_type_name, exam_date, published_at, subject_count}]`（只含已發布） |
| B480 | GET | `/children/{id}/exams/{exam_id}` | → `{exam_id, name, exam_type_name, exam_date, note, subjects: [{subject_name, full_score, score, is_absent, note}]}` |
| B217 | GET | `/notifications?unread_only` | → `{items: Notification[], total, unread_count}` |
| B218 | POST | `/notifications/{id}/read` | → `Notification` |
| B520 | POST | `/notifications/read-all` | → `{updated}` |
| B221 | GET | `/notification-preferences` | → `{items: [{event, label, line_enabled}]}`（7 個可關閉 LINE 的事件） |
| B222 | PUT | `/notification-preferences` | `{items: [{event, line_enabled}]}` → 同上 |

- `ParentLeave = {id, student_id, leave_type, leave_type_label, start_date, end_date, reason, status, created_by_type, created_at, cancelled_at, can_cancel（active 且 end_date ≥ 今天）, attachments: [{id, mime_type, size_bytes, created_at, url}]}`
- `ParentPickupRequest = {id, student_id, student_name, service_date, status, expected_arrival_at, reply_ready_eta, reply_message, replied_at, arrived_at, completed_at, picked_up_by_name, cancelled_at, created_at, can_cancel, can_mark_arrived}`
- `PickupPerson = {id, student_id, name, relation, phone, photo_url, created_at}`
- `ParentAuthorization = {id, student_id, service_date, pickup_person_id, proxy_name, proxy_phone, code_last4, status, effective_status（過期日的 active 顯示 expired）, verified_at, verification_method, created_at}`

---

## 15. WebSocket

通用信封：`{"type": str, "data": object, "sent_at": ISO8601 UTC}`。斷線時前端以輪詢對應 REST endpoint 補齊（推播為盡力而為）。

### `/api/ws/admin`（B226，員工 cookie）
- 連線後收到 `{"type":"ready","topics":[]}`，自動收個人通知。
- 送 `{"action":"subscribe","topics":["pickup","homework","attendance"]}` → `{"type":"subscribed","topics":[...]}`；無權限的 topic 回 `{"type":"error","code":"permission_denied","topics":[...]}`（pickup→pickup:read、homework→homework:read、attendance→attendance:read）。`unsubscribe` 同格式。
- 權限被移除時收到 `{"type":"unsubscribed","topics":[...],"reason":"permission_revoked"}`；帳號失效 close 4401；外站 Origin close 4403。

| topic | type | data |
|---|---|---|
| attendance | `attendance.updated` | `AttendanceRow` |
| attendance | `attendance.batch_updated` | `{items: AttendanceRow[]}` |
| attendance | `attendance.bulk_updated` | `{student_id, dates: [YYYY-MM-DD]}`（請假套用 / 取消，前端重抓當日清單） |
| homework | `homework.progress_updated` | `{student_id, service_date, items: HomeworkItem[], progress: Progress}` |
| pickup | `pickup.request_updated` | `PickupRequest`（新建、回覆、確認、抵達、完成、取消、過期都用此 type） |
| pickup | `pickup.authorization_updated` | `StaffAuthorization` |
| 個人 | `notification.created` | `Notification`（收件匣即時新增） |
| 個人 | `notification.transient` | `{event, title, body, payload}`（不進收件匣，例如 `pickup.arrived` 提示音） |

### `/api/ws/parent`（B227，家長 cookie）
- 連線後 `{"type":"ready","children":[student_id...]}`；家長不能 subscribe（回 `{"type":"error","code":"not_allowed"}`）。綁定變動時 `{"type":"children_changed","children":[...]}`。

| type | data |
|---|---|
| `homework.progress_updated` | `{student_id, date, items: [{title, subject_name, status}], overall_status, ready_eta, note}` |
| `pickup.request_updated` | `ParentPickupRequest` |
| `notification.created` | `Notification` |

### 通知事件（收件匣 `event` 值與 deep_link）

| event | 收件人 | 頻道 | deep_link |
|---|---|---|---|
| attendance.checked_in / checked_out | 家長 | in_app, line | `/attendance` |
| leave.created / leave.cancelled | 班級負責員工 + leaves:read 員工 | in_app, ws | `/leaves` |
| homework.eta_updated / homework.done | 家長 | in_app, line | `/homework` |
| pickup.requested | pickup:operate 員工 | in_app, ws | `/pickup` |
| pickup.replied | 發起的家長 | in_app, line | `/pickup` |
| pickup.arrived | pickup:operate 員工 | ws（transient） | `/pickup` |
| pickup.completed | 家長 | in_app, line | `/pickup` |
| pickup.cancelled | 家長取消 → pickup:operate 員工（in_app, ws）；員工取消 → 發起的家長（in_app, line） | 依收件人 | `/pickup` |
| exam.published | 應考學生家長 | in_app, line | `/exams` |
| binding.completed | 綁定的家長 | in_app | `/` |
