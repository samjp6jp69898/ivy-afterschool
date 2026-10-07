<script setup lang="ts">
// FRONTEND-062：參考資料頁「休息日」分頁（列表、新增 / 編輯 dialog、刪除）。
// 設計稿：docs/mockups/page-settings-reference.html（RefTable 段）。
// - 預設載入今天起 12 個月內（date_from = 今天、date_to = +365 天），勾「顯示過去日期」往前多載入一年；
//   依日期由近到遠排（後端回 date desc），過去的列變灰並加「已過」。
// - 日期新增時必填、編輯時唯讀（PATCH 只送 reason）；409 closed_day_exists 顯示在日期欄位下、dialog 不關。
// - 休息日不會因被引用而改為停用，也不在 lookups 快取中。
import { Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { closedDaysApi, type ClosedDay } from '@/api/referenceData'
import EmptyState from '@/components/common/EmptyState.vue'
import FormDialog from '@/components/common/FormDialog.vue'
import { useConfirmDelete } from '@/composables/useConfirmDelete'
import { useFormDirty } from '@/composables/useFormDirty'
import { PERMISSIONS } from '@/constants/permissions'
import { isApiError, type ISODate } from '@/shared/types/api'
import { addDays, formatDate, formatDateWithWeekday, todayTaipei } from '@/shared/utils/datetime'
import { errorMessage, validationFieldErrors } from '@/shared/utils/errorMessage'
import { usePermission } from '@/utils/permission'

type FieldKey = 'date' | 'reason'

const HINT = '休息日當天不會建立出勤名單'

const { can } = usePermission()
const canWrite = computed(() => can(PERMISSIONS.SETTINGS_WRITE))

const showPast = ref(false)
const range = ref<{ date_from: ISODate; date_to: ISODate }>(rangeOf(false))
const items = ref<ClosedDay[]>([])
const loading = ref(false)
const loadFailed = ref(false)
let loadSeq = 0

function rangeOf(past: boolean): { date_from: ISODate; date_to: ISODate } {
  const today = todayTaipei()
  return { date_from: past ? addDays(today, -365) : today, date_to: addDays(today, 365) }
}

const sortedItems = computed(() => [...items.value].sort((a, b) => a.date.localeCompare(b.date)))

const hint = computed(() =>
  showPast.value
    ? `顯示 ${formatDate(range.value.date_from)} ～ ${formatDate(range.value.date_to)}・${HINT}`
    : `顯示今天起 12 個月內・${HINT}`,
)

async function load(): Promise<void> {
  const seq = ++loadSeq
  range.value = rangeOf(showPast.value)
  loading.value = true
  try {
    const list = await closedDaysApi.list(range.value)
    if (seq !== loadSeq) return
    items.value = list
    loadFailed.value = false
  } catch {
    if (seq === loadSeq) loadFailed.value = true
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

void load()
watch(showPast, () => void load())

function isPast(row: ClosedDay): boolean {
  return row.date < todayTaipei()
}

function rowClass({ row }: { row: ClosedDay }): string {
  return isPast(row) ? 'ref-row-past' : ''
}

function dayLabel(row: ClosedDay): string {
  return `${formatDateWithWeekday(row.date)}${row.reason ? ` ${row.reason}` : ''}`
}

// ---- 新增 / 編輯 dialog ----
const dialogOpen = ref(false)
const editing = ref<ClosedDay | null>(null)
const saving = ref(false)
const form = reactive<{ date: ISODate | null; reason: string }>({ date: null, reason: '' })
const errors = reactive<Record<FieldKey, string>>({ date: '', reason: '' })
const { isDirty, markClean } = useFormDirty(form)

function openDialog(row: ClosedDay | null): void {
  editing.value = row
  Object.assign(form, { date: row?.date ?? null, reason: row?.reason ?? '' })
  errors.date = ''
  errors.reason = ''
  markClean()
  dialogOpen.value = true
}

async function submit(): Promise<void> {
  const row = editing.value
  errors.date = row || form.date ? '' : '請選擇日期'
  if (errors.date) return
  const reason = form.reason.trim() || null
  saving.value = true
  try {
    if (row) await closedDaysApi.update(row.id, { reason })
    else await closedDaysApi.create({ date: form.date!, reason })
    ElMessage.success(row ? '已更新' : '已新增')
    dialogOpen.value = false
    void load()
  } catch (err) {
    if (isApiError(err) && err.status === 409) {
      errors.date = err.message
      return
    }
    const fieldErrors = validationFieldErrors(err)
    const mapped = (['date', 'reason'] as FieldKey[]).filter((key) => fieldErrors[key])
    for (const key of mapped) errors[key] = fieldErrors[key]!
    if (!mapped.length) ElMessage.error(errorMessage(err, '儲存失敗，請稍後再試'))
  } finally {
    saving.value = false
  }
}

// ---- 刪除 ----
const { confirmDelete } = useConfirmDelete<ClosedDay>({
  request: (row) => closedDaysApi.remove(row.id),
  confirmMessage: (row) => `確定要刪除休息日「${dayLabel(row)}」嗎？`,
  onSuccess: () => void load(),
})
</script>

<template>
  <div class="ref-table">
    <div class="ref-toolbar">
      <el-checkbox v-model="showPast">
        顯示過去日期
      </el-checkbox>
      <span class="ref-toolbar__hint">{{ hint }}</span>
      <span class="ref-toolbar__spacer" />
      <el-button
        v-if="canWrite"
        type="primary"
        :icon="Plus"
        :disabled="loadFailed"
        @click="openDialog(null)"
      >
        新增休息日
      </el-button>
    </div>

    <EmptyState
      v-if="loadFailed"
      variant="error"
      title="無法載入休息日"
      description="網路連線中斷，請確認網路後再試一次"
    >
      <template #action>
        <el-button @click="load">
          重試
        </el-button>
      </template>
    </EmptyState>
    <el-table
      v-else
      v-loading="loading"
      :data="sortedItems"
      :row-class-name="rowClass"
    >
      <!-- @vue-generic {ClosedDay} -->
      <el-table-column
        label="日期"
        width="220"
      >
        <template #default="{ row }: { row: ClosedDay }">
          <span class="ref-date"><span class="ref-date__year">{{ row.date.slice(0, 4) }}</span><span>{{ formatDateWithWeekday(row.date) }}</span><el-tag
            v-if="isPast(row)"
            size="small"
            type="info"
            effect="plain"
            disable-transitions
          >已過</el-tag></span>
        </template>
      </el-table-column>
      <!-- @vue-generic {ClosedDay} -->
      <el-table-column
        label="原因"
        min-width="200"
        show-overflow-tooltip
      >
        <template #default="{ row }: { row: ClosedDay }">
          <span v-if="row.reason">{{ row.reason }}</span>
          <span
            v-else
            class="ref-blank"
          >—</span>
        </template>
      </el-table-column>
      <!-- @vue-generic {ClosedDay} -->
      <el-table-column
        v-if="canWrite"
        label="操作"
        width="120"
        fixed="right"
      >
        <template #default="{ row }: { row: ClosedDay }">
          <span class="ref-ops">
            <el-button
              link
              type="primary"
              @click="openDialog(row)"
            >
              編輯
            </el-button>
            <el-button
              link
              type="danger"
              @click="confirmDelete(row)"
            >
              刪除
            </el-button>
          </span>
        </template>
      </el-table-column>
      <template #empty>
        <EmptyState
          v-if="!loading"
          title="尚未建立休息日"
          :description="showPast ? '過去一年到未來 12 個月內都沒有設定休息日' : '今天起 12 個月內沒有設定休息日'"
        >
          <template
            v-if="canWrite && !showPast"
            #action
          >
            <el-button
              type="primary"
              :icon="Plus"
              @click="openDialog(null)"
            >
              新增休息日
            </el-button>
          </template>
        </EmptyState>
      </template>
    </el-table>

    <FormDialog
      v-model="dialogOpen"
      :title="editing ? '編輯休息日' : '新增休息日'"
      size="sm"
      :loading="saving"
      :dirty="isDirty"
      @submit="submit"
    >
      <el-form
        label-position="top"
        :disabled="saving"
        @submit.prevent
      >
        <el-alert
          v-if="!editing"
          class="ref-dialog-alert"
          type="info"
          :closable="false"
          show-icon
          title="設定為休息日後，當天不會建立出勤名單"
        />
        <el-form-item
          label="日期"
          :required="!editing"
          :error="errors.date"
        >
          <el-date-picker
            v-model="form.date"
            class="ref-date-picker"
            type="date"
            value-format="YYYY-MM-DD"
            format="YYYY/MM/DD"
            placeholder="選擇日期"
            :disabled="!!editing"
            @change="errors.date = ''"
          />
          <div
            v-if="editing"
            class="form-hint"
          >
            日期不可修改；要改日期請刪除後重新新增
          </div>
        </el-form-item>
        <el-form-item
          label="原因"
          :error="errors.reason"
        >
          <el-input
            v-model="form.reason"
            maxlength="100"
            show-word-limit
            placeholder="例如 國慶日、員工旅遊"
            @input="errors.reason = ''"
          />
        </el-form-item>
      </el-form>
    </FormDialog>
  </div>
</template>

<style scoped>
.ref-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  min-height: 32px;
  margin-bottom: 12px;
}

.ref-toolbar__hint {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.ref-toolbar__spacer {
  flex: 1;
}

.ref-table :deep(.ref-row-past td .cell) {
  color: var(--el-text-color-secondary);
}

.ref-date {
  display: inline-flex;
  gap: 6px;
  align-items: baseline;
  font-variant-numeric: tabular-nums;
}

.ref-date__year {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.ref-blank {
  color: var(--el-text-color-secondary);
}

.ref-ops {
  display: inline-flex;
  gap: 12px;
}

.ref-ops :deep(.el-button + .el-button) {
  margin-left: 0;
}

.ref-dialog-alert {
  margin-bottom: 16px;
}

.ref-date-picker {
  width: 100%;
}

.form-hint {
  width: 100%;
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}
</style>
