<script setup lang="ts">
// FRONTEND-061：參考資料頁「合作國小」分頁（列表、新增 / 編輯 dialog、刪除）。
// 設計稿：docs/mockups/page-settings-reference.html（RefTable 段）。
// - 依名稱排列；簡稱空白顯示「—」，停用的列文字變灰。缺 settings:write 時沒有新增按鈕與操作欄。
// - 簡稱空白送 null；422 欄位錯誤與 409 名稱重複顯示在欄位下方、dialog 不關；寫入成功後重新載入並讓 lookups 的國小快取失效。
// - 刪除被學生引用的國小時後端改為停用（deactivated），訊息依回傳值決定。
import { Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, reactive, ref } from 'vue'
import { schoolsApi, type DeleteResult, type School } from '@/api/referenceData'
import EmptyState from '@/components/common/EmptyState.vue'
import FormDialog from '@/components/common/FormDialog.vue'
import { useConfirmDelete } from '@/composables/useConfirmDelete'
import { useFormDirty } from '@/composables/useFormDirty'
import { PERMISSIONS } from '@/constants/permissions'
import { isApiError } from '@/shared/types/api'
import { errorMessage, validationFieldErrors } from '@/shared/utils/errorMessage'
import { useLookupsStore } from '@/stores/lookups'
import { usePermission } from '@/utils/permission'

type FieldKey = 'name' | 'short_name'

const { can } = usePermission()
const canWrite = computed(() => can(PERMISSIONS.SETTINGS_WRITE))
const lookups = useLookupsStore()

const items = ref<School[]>([])
const loading = ref(false)
const loadFailed = ref(false)
let loadSeq = 0

const sortedItems = computed(() => [...items.value].sort((a, b) => a.name.localeCompare(b.name, 'zh-Hant')))

async function load(): Promise<void> {
  const seq = ++loadSeq
  loading.value = true
  try {
    const list = await schoolsApi.list()
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
  lookups.invalidate('schools')
  void load()
}

function rowClass({ row }: { row: School }): string {
  return row.is_active ? '' : 'ref-row-inactive'
}

// ---- 新增 / 編輯 dialog ----
const dialogOpen = ref(false)
const editing = ref<School | null>(null)
const saving = ref(false)
const form = reactive<{ name: string; short_name: string; is_active: boolean }>({
  name: '',
  short_name: '',
  is_active: true,
})
const errors = reactive<Record<FieldKey, string>>({ name: '', short_name: '' })
const { isDirty, markClean } = useFormDirty(form)

function openDialog(row: School | null): void {
  editing.value = row
  Object.assign(form, {
    name: row?.name ?? '',
    short_name: row?.short_name ?? '',
    is_active: row?.is_active ?? true,
  })
  errors.name = ''
  errors.short_name = ''
  markClean()
  dialogOpen.value = true
}

async function submit(): Promise<void> {
  const name = form.name.trim()
  errors.name = name ? '' : '請輸入名稱'
  if (errors.name) return
  const body = { name, short_name: form.short_name.trim() || null, is_active: form.is_active }
  const row = editing.value
  saving.value = true
  try {
    if (row) await schoolsApi.update(row.id, body)
    else await schoolsApi.create(body)
    ElMessage.success(row ? '已更新' : '已新增')
    dialogOpen.value = false
    afterWrite()
  } catch (err) {
    if (isApiError(err) && err.status === 409) {
      errors.name = err.message
      return
    }
    const fieldErrors = validationFieldErrors(err)
    const mapped = (['name', 'short_name'] as FieldKey[]).filter((key) => fieldErrors[key])
    for (const key of mapped) errors[key] = fieldErrors[key]!
    if (!mapped.length) ElMessage.error(errorMessage(err, '儲存失敗，請稍後再試'))
  } finally {
    saving.value = false
  }
}

// ---- 刪除 ----
const { confirmDelete } = useConfirmDelete<School>({
  request: (row) => schoolsApi.remove(row.id),
  confirmMessage: (row) => `確定要刪除合作國小「${row.name}」嗎？`,
  successMessage: (result, row) =>
    (result as DeleteResult).deactivated ? `「${row.name}」已有資料引用，已改為停用` : '已刪除',
  onSuccess: afterWrite,
})
</script>

<template>
  <div class="ref-table">
    <div class="ref-toolbar">
      <span class="ref-toolbar__hint">學生資料的就讀國小下拉；簡稱用於學生列表與家長端</span>
      <span class="ref-toolbar__spacer" />
      <el-button
        v-if="canWrite"
        type="primary"
        :icon="Plus"
        :disabled="loadFailed"
        @click="openDialog(null)"
      >
        新增合作國小
      </el-button>
    </div>

    <EmptyState
      v-if="loadFailed"
      variant="error"
      title="無法載入合作國小"
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
      <!-- @vue-generic {School} -->
      <el-table-column
        label="簡稱"
        width="160"
      >
        <template #default="{ row }: { row: School }">
          <span v-if="row.short_name">{{ row.short_name }}</span>
          <span
            v-else
            class="ref-blank"
          >—</span>
        </template>
      </el-table-column>
      <!-- @vue-generic {School} -->
      <el-table-column
        label="狀態"
        width="100"
      >
        <template #default="{ row }: { row: School }">
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
      <!-- @vue-generic {School} -->
      <el-table-column
        v-if="canWrite"
        label="操作"
        width="120"
        fixed="right"
      >
        <template #default="{ row }: { row: School }">
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
          title="尚未建立合作國小"
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
              新增合作國小
            </el-button>
          </template>
        </EmptyState>
      </template>
    </el-table>

    <FormDialog
      v-model="dialogOpen"
      :title="editing ? '編輯合作國小' : '新增合作國小'"
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
            maxlength="50"
            show-word-limit
            placeholder="例如 某某國民小學"
            @input="errors.name = ''"
          />
        </el-form-item>
        <el-form-item
          label="簡稱"
          :error="errors.short_name"
        >
          <el-input
            v-model="form.short_name"
            maxlength="20"
            show-word-limit
            placeholder="例如 某某國小"
            @input="errors.short_name = ''"
          />
          <div class="form-hint">
            學生列表與家長端顯示用；空白時顯示完整名稱
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
