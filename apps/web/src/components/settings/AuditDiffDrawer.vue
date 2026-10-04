<script setup lang="ts">
// FRONTEND-073：稽核紀錄 before / after 差異檢視。
// 設計稿：docs/mockups/page-settings-audit.html（AuditDiffDrawer 段）。
// 差異表取 before / after 的 key 聯集；巢狀物件以「.」展開到第二層，更深的與陣列以 JSON 字串顯示。
// 遮罩值（***、****a91f）原樣顯示，不嘗試還原。
import { DocumentCopy } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'
import type { AuditActorType, AuditLog } from '@/api/auditLogs'
import EmptyState from '@/components/common/EmptyState.vue'
import { formatDateTime } from '@/shared/utils/datetime'

const props = defineProps<{
  modelValue: boolean
  log: AuditLog | null
  actionLabel: string
}>()

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

type DiffKind = 'added' | 'removed' | 'changed' | 'same'

interface DiffRow {
  key: string
  kind: DiffKind
  before: unknown
  after: unknown
  inBefore: boolean
  inAfter: boolean
}

const ENTITY_LABELS: Record<string, string> = {
  staff_user: '員工帳號',
  role: '角色',
  system_setting: '系統設定',
  student: '學生',
  student_import: '學生匯入',
  academic_year: '學年度',
  guardian: '監護人',
  student_attendance: '出勤',
  exam: '考試',
  exam_score: '成績',
  pickup_request: '接送請求',
  pickup_authorization: '代理接送授權',
}

const ACTOR_TAGS: Record<AuditActorType, { label: string; type: 'info' | 'success' | 'warning' | 'primary' }> = {
  staff: { label: '員工', type: 'info' },
  parent: { label: '家長', type: 'success' },
  system: { label: '系統', type: 'warning' },
  device: { label: '裝置', type: 'primary' },
}

const showSame = ref(false)

watch(
  () => props.log,
  () => {
    showSame.value = false
  },
)

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

function flatten(obj: Record<string, unknown> | null): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  if (!obj) return out
  for (const [k, v] of Object.entries(obj)) {
    if (isPlainObject(v)) {
      for (const [k2, v2] of Object.entries(v)) {
        out[`${k}.${k2}`] = v2 !== null && typeof v2 === 'object' ? JSON.stringify(v2) : v2
      }
    } else {
      out[k] = v
    }
  }
  return out
}

function display(v: unknown): string {
  return typeof v === 'string' ? v : JSON.stringify(v)
}

const rows = computed<DiffRow[]>(() => {
  if (!props.log) return []
  const before = flatten(props.log.before)
  const after = flatten(props.log.after)
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
  return keys.map((key) => {
    const inBefore = key in before
    const inAfter = key in after
    let kind: DiffKind = 'changed'
    if (!inBefore) kind = 'added'
    else if (!inAfter) kind = 'removed'
    else if (JSON.stringify(before[key]) === JSON.stringify(after[key])) kind = 'same'
    return { key, kind, before: before[key], after: after[key], inBefore, inAfter }
  })
})

const visibleRows = computed(() => (showSame.value ? rows.value : rows.value.filter((r) => r.kind !== 'same')))
const noData = computed(() => props.log !== null && props.log.before == null && props.log.after == null)
const counts = computed(() => {
  const c: Record<DiffKind, number> = { changed: 0, added: 0, removed: 0, same: 0 }
  for (const r of rows.value) c[r.kind] += 1
  return c
})

const actorTag = computed(() => (props.log ? ACTOR_TAGS[props.log.actor_type] : null))
const entityLabel = computed(() =>
  props.log?.entity_type ? (ENTITY_LABELS[props.log.entity_type] ?? props.log.entity_type) : '—',
)

// 關閉一律交給父層（emit false 後由 modelValue 驅動關閉動畫）
function requestClose(): void {
  emit('update:modelValue', false)
}

async function copyEntityId(): Promise<void> {
  if (!props.log?.entity_id) return
  try {
    await navigator.clipboard.writeText(props.log.entity_id)
    ElMessage.success('已複製')
  } catch {
    ElMessage.error('無法複製，請手動選取')
  }
}
</script>

<template>
  <el-drawer
    :model-value="modelValue"
    :title="actionLabel"
    size="600px"
    append-to-body
    :before-close="requestClose"
  >
    <template v-if="log">
      <el-descriptions
        class="diff-info"
        :column="1"
        border
        size="small"
      >
        <el-descriptions-item label="時間">
          {{ formatDateTime(log.created_at) }}
        </el-descriptions-item>
        <el-descriptions-item label="操作者">
          {{ log.actor_name || '—' }}
          <el-tag
            v-if="actorTag"
            size="small"
            :type="actorTag.type"
            effect="plain"
            disable-transitions
          >
            {{ actorTag.label }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="動作">
          <span class="diff-mono">{{ log.action }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="對象">
          <span class="diff-entity">
            {{ entityLabel }}
            <span class="diff-mono diff-secondary">{{ log.entity_id }}</span>
            <el-button
              v-if="log.entity_id"
              link
              type="primary"
              size="small"
              :icon="DocumentCopy"
              aria-label="複製對象 ID"
              @click="copyEntityId"
            >
              複製
            </el-button>
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="IP">
          <span class="diff-mono">{{ log.ip || '—' }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="User-Agent">
          <el-tooltip
            v-if="log.user_agent"
            :content="log.user_agent"
            placement="top"
            :show-after="300"
          >
            <span class="diff-ua diff-secondary">{{ log.user_agent }}</span>
          </el-tooltip>
          <span v-else>—</span>
        </el-descriptions-item>
      </el-descriptions>

      <div class="diff-head">
        <h3 class="diff-head__title">
          欄位變更
        </h3>
        <template v-if="!noData">
          <span class="diff-head__summary">
            變更 {{ counts.changed }}・新增 {{ counts.added }}・刪除 {{ counts.removed }}・未變更 {{ counts.same }}
          </span>
          <el-switch
            v-model="showSame"
            :disabled="!counts.same"
            active-text="顯示未變更欄位"
          />
        </template>
      </div>

      <EmptyState
        v-if="noData"
        variant="inline"
        title="此紀錄沒有欄位變更資料"
      />
      <EmptyState
        v-else-if="!visibleRows.length"
        variant="inline"
        title="沒有欄位內容變更"
        description="遮罩後的值相同或欄位皆未變更；打開「顯示未變更欄位」可查看全部"
      />
      <table
        v-else
        class="diff-table"
      >
        <colgroup>
          <col class="c-field">
          <col>
          <col>
        </colgroup>
        <thead>
          <tr>
            <th>欄位</th>
            <th>變更前</th>
            <th>變更後</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in visibleRows"
            :key="r.key"
            class="diff-row"
            :class="`is-${r.kind}`"
          >
            <td class="diff-field diff-mono">
              {{ r.key }}
            </td>
            <td
              class="diff-cell"
              :class="{ 'is-removed': r.kind === 'removed', 'is-old': r.kind === 'changed' }"
            >
              <span
                v-if="!r.inBefore"
                class="diff-absent"
              >—</span>
              <template v-else>
                <el-tag
                  v-if="r.kind === 'removed'"
                  class="diff-badge"
                  size="small"
                  type="danger"
                  disable-transitions
                >
                  刪除
                </el-tag>
                <span
                  v-if="r.before === null"
                  class="diff-null"
                >（空）</span>
                <span v-else>{{ display(r.before) }}</span>
              </template>
            </td>
            <td
              class="diff-cell"
              :class="{ 'is-added': r.kind === 'added' }"
            >
              <span
                v-if="!r.inAfter"
                class="diff-absent"
              >—</span>
              <template v-else>
                <el-tag
                  v-if="r.kind === 'added'"
                  class="diff-badge"
                  size="small"
                  type="success"
                  disable-transitions
                >
                  新增
                </el-tag>
                <span
                  v-if="r.after === null"
                  class="diff-null"
                >（空）</span>
                <span v-else>{{ display(r.after) }}</span>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
    </template>
  </el-drawer>
</template>

<style scoped>
.diff-info {
  margin-bottom: 20px;
}

.diff-info :deep(.el-descriptions__label) {
  width: 96px;
}

.diff-mono {
  font-family: var(--el-font-family-mono, ui-monospace, monospace);
}

.diff-secondary {
  color: var(--el-text-color-secondary);
}

.diff-ua {
  display: block;
  max-width: 340px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.diff-entity {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.diff-head {
  display: flex;
  gap: 12px;
  align-items: center;
  margin: 0 0 8px;
}

.diff-head__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.diff-head__summary {
  flex: 1;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.diff-table {
  width: 100%;
  font-size: 13px;
  border-collapse: collapse;
  table-layout: fixed;
}

.diff-table th {
  padding: 8px 10px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  text-align: left;
  background: var(--el-fill-color-light);
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.diff-table td {
  padding: 8px 10px;
  word-break: break-all;
  white-space: pre-wrap;
  vertical-align: top;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}

.diff-table col.c-field {
  width: 30%;
}

.diff-field {
  color: var(--el-text-color-regular);
}

.diff-cell.is-added {
  color: var(--el-color-success-dark-2);
  background: var(--el-color-success-light-9);
}

.diff-cell.is-removed {
  color: var(--el-color-danger);
  text-decoration: line-through;
  background: var(--el-color-danger-light-9);
}

.diff-cell.is-old {
  color: var(--el-text-color-secondary);
}

.diff-row.is-same td {
  color: var(--el-text-color-placeholder);
}

.diff-absent {
  display: inline-block;
  color: var(--el-text-color-placeholder);
  text-decoration: none;
}

.diff-null {
  font-style: italic;
  color: var(--el-text-color-placeholder);
}

.diff-badge {
  display: inline-block;
  margin-right: 6px;
  text-decoration: none;
}
</style>
