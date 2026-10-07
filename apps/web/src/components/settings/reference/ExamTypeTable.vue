<script setup lang="ts">
// FRONTEND-060：參考資料頁「考試類型」分頁（列表、新增 / 編輯 dialog、刪除）。
// 設計稿：docs/mockups/page-settings-reference.html（RefTable 段，與科目共用版面）。
// - 依排序、再依名稱排列；停用的列文字變灰。缺 settings:write 時沒有新增按鈕與操作欄。
// - 422 欄位錯誤與 409 名稱重複顯示在欄位下方、dialog 不關；寫入成功後重新載入並讓 lookups 的考試類型快取失效。
// - 刪除被引用的類型時後端改為停用（deactivated），訊息依回傳值決定。
import { Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, reactive, ref } from 'vue'
import { examTypesApi, type DeleteResult, type ExamType } from '@/api/referenceData'
import EmptyState from '@/components/common/EmptyState.vue'
import FormDialog from '@/components/common/FormDialog.vue'
import { useConfirmDelete } from '@/composables/useConfirmDelete'
import { useFormDirty } from '@/composables/useFormDirty'
import { PERMISSIONS } from '@/constants/permissions'
import { isApiError } from '@/shared/types/api'
import { errorMessage, validationFieldErrors } from '@/shared/utils/errorMessage'
import { useLookupsStore } from '@/stores/lookups'
import { usePermission } from '@/utils/permission'

type FieldKey = 'name' | 'sort_order'

const { can } = usePermission()
const canWrite = computed(() => can(PERMISSIONS.SETTINGS_WRITE))
const lookups = useLookupsStore()

const items = ref<ExamType[]>([])
const loading = ref(false)
const loadFailed = ref(false)
let loadSeq = 0

const sortedItems = computed(() =>
  [...items.value].sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name, 'zh-Hant')),
)

async function load(): Promise<void> {
  const seq = ++loadSeq
  loading.value = true
  try {
    const list = await examTypesApi.list()
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

function afterWrite(): void {
  lookups.invalidate('examTypes')
  void load()
}

function rowClass({ row }: { row: ExamType }): string {
  return row.is_active ? '' : 'ref-row-inactive'
}

// ---- 新增 / 編輯 dialog ----
const dialogOpen = ref(false)
const editing = ref<ExamType | null>(null)
const saving = ref(false)
const form = reactive<{ name: string; sort_order: number; is_active: boolean }>({
  name: '',
  sort_order: 0,
  is_active: true,
})
const errors = reactive<Record<FieldKey, string>>({ name: '', sort_order: '' })
const { isDirty, markClean } = useFormDirty(form)

function openDialog(row: ExamType | null): void {
  editing.value = row
  const nextSort = Math.max(0, ...items.value.map((t) => t.sort_order)) + 1
  Object.assign(form, {
    name: row?.name ?? '',
    sort_order: row?.sort_order ?? nextSort,
    is_active: row?.is_active ?? true,
  })
  errors.name = ''
  errors.sort_order = ''
  markClean()
  dialogOpen.value = true
}

function onSortOrder(value: number | null | undefined): void {
  form.sort_order = value ?? 0
}

async function submit(): Promise<void> {
  const name = form.name.trim()
  errors.name = name ? '' : '請輸入名稱'
  if (errors.name) return
  const body = { name, sort_order: form.sort_order, is_active: form.is_active }
  const row = editing.value
  saving.value = true
  try {
    if (row) await examTypesApi.update(row.id, body)
    else await examTypesApi.create(body)
    ElMessage.success(row ? '已更新' : '已新增')
    dialogOpen.value = false
    afterWrite()
  } catch (err) {
    if (isApiError(err) && err.status === 409) {
      errors.name = err.message
      return
    }
    const fieldErrors = validationFieldErrors(err)
    const mapped = (['name', 'sort_order'] as FieldKey[]).filter((key) => fieldErrors[key])
    for (const key of mapped) errors[key] = fieldErrors[key]!
    if (!mapped.length) ElMessage.error(errorMessage(err, '儲存失敗，請稍後再試'))
  } finally {
    saving.value = false
  }
}

// ---- 刪除 ----
const { confirmDelete } = useConfirmDelete<ExamType>({
  request: (row) => examTypesApi.remove(row.id),
  confirmMessage: (row) => `確定要刪除考試類型「${row.name}」嗎？`,
  successMessage: (result, row) =>
    (result as DeleteResult).deactivated ? `「${row.name}」已有資料引用，已改為停用` : '已刪除',
  onSuccess: afterWrite,
})
</script>

<template>
  <div class="ref-table">
    <div class="ref-toolbar">
      <span class="ref-toolbar__hint">建立考試時的類型下拉依排序顯示；停用的類型不會出現在新增資料的下拉選單</span>
      <span class="ref-toolbar__spacer" />
      <el-button
        v-if="canWrite"
        type="primary"
        :icon="Plus"
        :disabled="loadFailed"
        @click="openDialog(null)"
      >
        新增考試類型
      </el-button>
    </div>

    <EmptyState
      v-if="loadFailed"
      variant="error"
      title="無法載入考試類型"
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
      <el-table-column
        prop="name"
        label="名稱"
        min-width="200"
        show-overflow-tooltip
      />
      <el-table-column
        prop="sort_order"
        label="排序"
        width="90"
        align="right"
      />
      <!-- @vue-generic {ExamType} -->
      <el-table-column
        label="狀態"
        width="100"
      >
        <template #default="{ row }: { row: ExamType }">
          <el-tag
            v-if="row.is_active"
            size="small"
            type="success"
            disable-transitions
          >
            啟用
          </el-tag>
          <el-tag
            v-else
            size="small"
            type="info"
            effect="plain"
            disable-transitions
          >
            停用
          </el-tag>
        </template>
      </el-table-column>
      <!-- @vue-generic {ExamType} -->
      <el-table-column
        v-if="canWrite"
        label="操作"
        width="120"
        fixed="right"
      >
        <template #default="{ row }: { row: ExamType }">
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
          title="尚未建立考試類型"
        >
          <template
            v-if="canWrite"
            #action
          >
            <el-button
              type="primary"
              :icon="Plus"
              @click="openDialog(null)"
            >
              新增考試類型
            </el-button>
          </template>
        </EmptyState>
      </template>
    </el-table>

    <FormDialog
      v-model="dialogOpen"
      :title="editing ? '編輯考試類型' : '新增考試類型'"
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
        <el-form-item
          label="名稱"
          required
          :error="errors.name"
        >
          <el-input
            v-model="form.name"
            maxlength="20"
            show-word-limit
            @input="errors.name = ''"
          />
        </el-form-item>
        <el-form-item
          label="排序"
          :error="errors.sort_order"
        >
          <el-input-number
            class="ref-sort"
            :model-value="form.sort_order"
            :min="0"
            :step="1"
            step-strictly
            controls-position="right"
            @update:model-value="onSortOrder"
          />
          <div class="form-hint">
            數字小的排前面
          </div>
        </el-form-item>
        <el-form-item label="啟用">
          <el-switch v-model="form.is_active" />
          <span class="ref-switch-hint">{{ form.is_active ? '會出現在下拉選單' : '不會出現在新增資料的下拉選單' }}</span>
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

.ref-table :deep(.ref-row-inactive td .cell) {
  color: var(--el-text-color-secondary);
}

.ref-ops {
  display: inline-flex;
  gap: 12px;
}

.ref-ops :deep(.el-button + .el-button) {
  margin-left: 0;
}

.ref-sort {
  width: 140px;
}

.ref-switch-hint {
  margin-left: 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.form-hint {
  width: 100%;
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}
</style>
