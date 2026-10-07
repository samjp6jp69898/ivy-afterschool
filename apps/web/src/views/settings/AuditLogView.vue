<script setup lang="ts">
// FRONTEND-072：稽核紀錄查詢頁（/settings/audit，audit:read）。
// 設計稿：docs/mockups/page-settings-audit.html。本頁只查詢，沒有寫入操作。
// 篩選列沿用 AdminListToolbar 的版面樣式但不用該元件（本頁沒有關鍵字搜尋）。
// 下拉的「全部」用哨兵值（EP 的 el-select 把 '' 當成未選而顯示 placeholder），寫入 filters 時轉成 ''，
// usePagedList 送出前會去掉空值。日期區間一定有值（不可清空），避免一次撈全部。
// el-table-column 是泛型元件，slot 的 row 預設型別是 Record<PropertyKey, any>；
// 每欄前的 `@vue-generic {AuditLog}` 註解指定泛型，讓 row 是 AuditLog（vue-tsc 專用，不影響執行）。
import { computed, ref } from 'vue'
import { listAuditLogs, type AuditActorType, type AuditLog, type AuditLogQuery } from '@/api/auditLogs'
import EmptyState from '@/components/common/EmptyState.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import AuditDiffDrawer from '@/components/settings/AuditDiffDrawer.vue'
import { usePagedList } from '@/composables/usePagedList'
import { AUDIT_ACTOR_TAGS, AUDIT_ENTITY_LABELS, type AuditActorTagType } from '@/constants/audit'
import { addDays, formatDateTime, todayTaipei } from '@/shared/utils/datetime'

/** domain_spec M1 與各 BACKEND task 定義的稽核 action；未列出的顯示原文 */
const AUDIT_ACTION_LABELS: Record<string, string> = {
  'role.create': '新增角色',
  'role.update': '修改角色',
  'role.delete': '刪除角色',
  'staff_user.create': '新增員工帳號',
  'staff_user.update': '修改員工帳號',
  'staff_user.reset_password': '重設員工密碼',
  'staff_user.deactivate': '停用員工帳號',
  'staff_user.activate': '重新啟用員工帳號',
  'settings.update': '修改系統設定',
  'student.import': '匯入學生',
  'student.promote_grade': '學年升級',
  'student.sensitive_update': '修改學生敏感資料',
  'student.purge': '永久刪除學生',
  'student.close_out': '學生退班 / 暫停收尾',
  'guardian.binding_code_issue': '產生家長綁定碼',
  'guardian.unbind': '解除家長綁定',
  'attendance.amend': '出勤改判',
  'exam_score.update': '修改已發布成績',
  'exam.unpublish': '取消發布成績',
  'pickup.override_complete': '代理接送強制完成',
  'pickup.visual_match': '代理接送目視核對',
}

/** 動作類別 → action_prefix */
const ACTION_CATEGORIES = [
  { value: 'staff_user.', label: '帳號' },
  { value: 'role.', label: '角色' },
  { value: 'settings.', label: '系統設定' },
  { value: 'student.', label: '學生' },
  { value: 'guardian.', label: '監護人' },
  { value: 'attendance.', label: '出勤改判' },
  { value: 'exam_score.', label: '成績修改' },
  { value: 'exam.', label: '考試發布' },
  { value: 'pickup.', label: '接送強制完成 / 目視核對' },
] as const

const ALL = '__all__'
const PAGE_SIZE = 50
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

interface AuditFilters extends Record<string, unknown> {
  action_prefix: string
  entity_type: string
  actor_type: string
  date_from: string
  date_to: string
}

function defaultFilters(): AuditFilters {
  const today = todayTaipei()
  return { action_prefix: '', entity_type: '', actor_type: '', date_from: addDays(today, -6), date_to: today }
}

const list = usePagedList<AuditLog, AuditFilters>({
  // usePagedList 送出前已去掉空字串，actor_type 只會是下拉選項的值
  fetch: (params) => listAuditLogs(params as AuditLogQuery),
  initialFilters: defaultFilters(),
  pageSize: PAGE_SIZE,
  syncQuery: true,
})
const { items, total, page, filters, loading, error } = list

function selectModel(key: 'action_prefix' | 'entity_type' | 'actor_type') {
  return computed({
    get: () => filters[key] || ALL,
    set: (v: string) => list.setFilters({ [key]: v === ALL ? '' : v }),
  })
}

const actionPrefix = selectModel('action_prefix')
const entityType = selectModel('entity_type')
const actorType = selectModel('actor_type')

const dateRange = computed({
  get: (): [string, string] => [filters.date_from, filters.date_to],
  set: (v: [string, string] | null) => {
    if (!v) return
    list.setFilters({ date_from: v[0], date_to: v[1] })
  },
})

const isFiltered = computed(() => {
  const d = defaultFilters()
  return (Object.keys(d) as (keyof AuditFilters)[]).some((k) => filters[k] !== d[k])
})

function resetFilters(): void {
  list.setFilters(defaultFilters())
}

/** 台北日期 → 本地時區同一天的 00:00（日期選擇器以本地時區格式化 value-format） */
function localDate(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y!, m! - 1, d!)
}

function localIso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function lastDays(n: number): () => [Date, Date] {
  return () => {
    const today = todayTaipei()
    return [localDate(addDays(today, -(n - 1))), localDate(today)]
  }
}

const shortcuts = [
  { text: '今天', value: lastDays(1) },
  { text: '近 7 天', value: lastDays(7) },
  { text: '近 30 天', value: lastDays(30) },
]

const isFutureDate = (d: Date): boolean => localIso(d) > todayTaipei()

function actionLabel(action: string): string | null {
  return AUDIT_ACTION_LABELS[action] ?? null
}

function entityLabel(type: string | null): string {
  if (!type) return '—'
  return AUDIT_ENTITY_LABELS[type] ?? type
}

function actorTag(type: string): { label: string; type: AuditActorTagType } {
  return AUDIT_ACTOR_TAGS[type as AuditActorType] ?? { label: type, type: 'info' }
}

/** UUID 只顯示前 8 碼；設定 key、學年度等非 UUID 的 id 完整顯示 */
function shortId(id: string | null): string {
  if (!id) return '—'
  return UUID_RE.test(id) ? id.slice(0, 8) : id
}

// 關閉抽屜時保留 diffLog，避免離場動畫期間內容提前消失
const diffOpen = ref(false)
const diffLog = ref<AuditLog | null>(null)
const diffActionLabel = computed(() => (diffLog.value ? (actionLabel(diffLog.value.action) ?? diffLog.value.action) : ''))

function openDiff(row: AuditLog): void {
  diffLog.value = row
  diffOpen.value = true
}

const showCount = computed(() => !error.value && !(loading.value && !items.value.length))
</script>

<template>
  <div class="audit-log-view">
    <PageHeader
      title="稽核紀錄"
      subtitle="帳號、角色、系統設定與重要營運異動的紀錄；紀錄只能查詢，無法修改或刪除"
    />

    <section class="panel">
      <div class="list-toolbar">
        <div
          class="list-toolbar__filter"
          data-test="filter-action"
        >
          <span class="list-toolbar__filter-label">動作類別</span>
          <el-select
            v-model="actionPrefix"
            aria-label="動作類別"
            style="width: 170px"
          >
            <el-option
              label="全部"
              :value="ALL"
            />
            <el-option
              v-for="o in ACTION_CATEGORIES"
              :key="o.value"
              :label="o.label"
              :value="o.value"
            />
          </el-select>
        </div>
        <div
          class="list-toolbar__filter"
          data-test="filter-entity"
        >
          <span class="list-toolbar__filter-label">對象類型</span>
          <el-select
            v-model="entityType"
            aria-label="對象類型"
            filterable
            allow-create
            default-first-option
            style="width: 150px"
          >
            <el-option
              label="全部"
              :value="ALL"
            />
            <el-option
              v-for="(label, code) in AUDIT_ENTITY_LABELS"
              :key="code"
              :label="label"
              :value="code"
            />
          </el-select>
        </div>
        <div
          class="list-toolbar__filter"
          data-test="filter-actor"
        >
          <span class="list-toolbar__filter-label">操作者</span>
          <el-select
            v-model="actorType"
            aria-label="操作者"
            style="width: 110px"
          >
            <el-option
              label="全部"
              :value="ALL"
            />
            <el-option
              v-for="(tag, code) in AUDIT_ACTOR_TAGS"
              :key="code"
              :label="tag.label"
              :value="code"
            />
          </el-select>
        </div>
        <div
          class="list-toolbar__filter"
          data-test="filter-date"
        >
          <span class="list-toolbar__filter-label">日期</span>
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            :clearable="false"
            unlink-panels
            range-separator="～"
            start-placeholder="開始日"
            end-placeholder="結束日"
            :shortcuts="shortcuts"
            :disabled-date="isFutureDate"
            style="width: 250px"
          />
        </div>
        <el-button
          text
          type="primary"
          :disabled="!isFiltered"
          data-test="reset-filters"
          @click="resetFilters"
        >
          重設
        </el-button>
        <span class="list-toolbar__spacer" />
        <span
          v-if="showCount"
          class="list-toolbar__count"
        >共 {{ total }} 筆</span>
      </div>

      <EmptyState
        v-if="error"
        variant="error"
        title="無法載入稽核紀錄"
        :description="error"
      >
        <template #action>
          <el-button
            data-test="retry"
            @click="list.reload()"
          >
            重試
          </el-button>
        </template>
      </EmptyState>

      <template v-else>
        <el-table
          v-loading="loading"
          class="audit-table"
          :data="items"
          border
          row-key="id"
          @row-click="openDiff"
        >
          <!-- @vue-generic {AuditLog} -->
          <el-table-column
            label="時間"
            width="150"
          >
            <template #default="{ row }: { row: AuditLog }">
              <span class="audit-time">{{ formatDateTime(row.created_at) }}</span>
            </template>
          </el-table-column>
          <!-- @vue-generic {AuditLog} -->
          <el-table-column
            label="操作者"
            min-width="150"
          >
            <template #default="{ row }: { row: AuditLog }">
              <div class="audit-actor">
                <span class="audit-actor__name">{{ row.actor_name || '—' }}</span>
                <el-tag
                  size="small"
                  :type="actorTag(row.actor_type).type"
                  effect="plain"
                  disable-transitions
                >
                  {{ actorTag(row.actor_type).label }}
                </el-tag>
              </div>
            </template>
          </el-table-column>
          <!-- @vue-generic {AuditLog} -->
          <el-table-column
            label="動作"
            min-width="190"
          >
            <template #default="{ row }: { row: AuditLog }">
              <div
                v-if="actionLabel(row.action)"
                class="audit-action__label"
                data-test="action-label"
              >
                {{ actionLabel(row.action) }}
              </div>
              <div class="audit-action__code audit-mono">
                {{ row.action }}
              </div>
            </template>
          </el-table-column>
          <!-- @vue-generic {AuditLog} -->
          <el-table-column
            label="對象"
            min-width="170"
          >
            <template #default="{ row }: { row: AuditLog }">
              <div>{{ entityLabel(row.entity_type) }}</div>
              <div class="audit-entity__id audit-mono">
                {{ shortId(row.entity_id) }}
              </div>
            </template>
          </el-table-column>
          <!-- @vue-generic {AuditLog} -->
          <el-table-column
            label="IP"
            width="130"
          >
            <template #default="{ row }: { row: AuditLog }">
              <span class="audit-ip audit-mono">{{ row.ip || '—' }}</span>
            </template>
          </el-table-column>
          <!-- @vue-generic {AuditLog} -->
          <el-table-column
            label=""
            width="72"
            align="center"
          >
            <template #default="{ row }: { row: AuditLog }">
              <el-button
                link
                type="primary"
                data-test="open-detail"
                @click.stop="openDiff(row)"
              >
                詳情
              </el-button>
            </template>
          </el-table-column>
          <template #empty>
            <EmptyState
              v-if="!loading"
              variant="inline"
              title="這段期間沒有稽核紀錄"
              description="試著放寬日期區間或調整篩選條件"
            />
          </template>
        </el-table>
        <div
          v-if="total > 0"
          class="list-pager"
        >
          <el-pagination
            :current-page="page"
            :page-size="PAGE_SIZE"
            :total="total"
            layout="total, prev, pager, next"
            background
            @update:current-page="list.setPage"
          />
        </div>
      </template>
    </section>

    <AuditDiffDrawer
      v-model="diffOpen"
      :log="diffLog"
      :action-label="diffActionLabel"
    />
  </div>
</template>

<style scoped>
.panel {
  padding: 16px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

/* 與 AdminListToolbar 相同的篩選列版面 */
.list-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
}

.list-toolbar__filter {
  display: flex;
  gap: 6px;
  align-items: center;
}

.list-toolbar__filter-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.list-toolbar__spacer {
  flex: 1 1 auto;
}

.list-toolbar__count {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.list-pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.audit-table :deep(.el-table__row) {
  cursor: pointer;
}

.audit-mono {
  font-family: var(--el-font-family-mono, ui-monospace, monospace);
}

.audit-time {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.audit-actor {
  display: flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
}

.audit-actor__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.audit-action__label {
  color: var(--el-text-color-primary);
}

.audit-action__code,
.audit-entity__id {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.audit-ip {
  font-size: 13px;
  color: var(--el-text-color-regular);
}
</style>
