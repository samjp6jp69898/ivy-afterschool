<script setup lang="ts">
// FRONTEND-155：新增 / 編輯單一學生的作業項目。設計稿：docs/mockups/page-homework-board.html。
// - 以 FormDialog sm 呈現；送出只 emit submit（內容去頭尾空白），由看板呼叫 API、成功後關閉；
//   編輯時有沒有變更由看板比對原值決定，本元件照樣 emit。
// - 科目可不選；編輯中的項目若科目已停用（不在 lookups 的啟用清單），仍以原名稱列為選項。
// - 「最近使用」由 recentHomeworkTitles 讀寫（與 HomeworkBatchAddDialog 共用），通過驗證、emit submit 時記錄。
import { computed, reactive, ref, watch } from 'vue'
import FormDialog from '@/components/common/FormDialog.vue'
import { useFormDirty } from '@/composables/useFormDirty'
import { useLookupsStore, type LookupOption } from '@/stores/lookups'
import { loadRecentTitles, rememberRecentTitle } from './recentHomeworkTitles'

/** 與 api/homework.ts 的 HomeworkItem 相容的子集（本元件只用到這些欄位） */
export interface HomeworkItemDialogItem {
  subject_id: string | null
  subject_name?: string | null
  title: string
}

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    studentName: string
    item?: HomeworkItemDialogItem
    loading: boolean
    error: string | null
  }>(),
  { item: undefined },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [body: { subject_id: string | null; title: string }]
}>()

const lookups = useLookupsStore()

const form = reactive<{ subject_id: string | null; title: string }>({ subject_id: null, title: '' })
const titleError = ref('')
const recentTitles = ref<string[]>([])
const { isDirty, markClean } = useFormDirty(form)

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    form.subject_id = props.item?.subject_id ?? null
    form.title = props.item?.title ?? ''
    titleError.value = ''
    markClean()
    recentTitles.value = loadRecentTitles()
    lookups.ensure('subjects').catch(() => {
      // 科目可不選：清單載入失敗時下拉是空的，仍可送出
    })
  },
  { immediate: true },
)

const subjectOptions = computed<LookupOption[]>(() => {
  const options = lookups.subjectOptions
  const item = props.item
  if (item?.subject_id && item.subject_name && !options.some((o) => o.value === item.subject_id)) {
    return [...options, { label: item.subject_name, value: item.subject_id }]
  }
  return options
})

function applyRecent(title: string): void {
  form.title = title
  titleError.value = ''
}

function submit(): void {
  const title = form.title.trim()
  titleError.value = title ? '' : '請輸入作業內容'
  if (titleError.value) return
  rememberRecentTitle(title)
  emit('submit', { subject_id: form.subject_id, title })
}
</script>

<template>
  <FormDialog
    :model-value="modelValue"
    :title="`${item ? '編輯作業' : '新增作業'}：${studentName}`"
    size="sm"
    :loading="loading"
    :dirty="isDirty"
    @update:model-value="emit('update:modelValue', $event)"
    @submit="submit"
  >
    <el-alert
      v-if="error"
      class="homework-item__error"
      type="error"
      :closable="false"
      show-icon
      :title="error"
    />
    <el-form
      label-position="top"
      :disabled="loading"
      @submit.prevent
    >
      <el-form-item label="科目">
        <el-select
          v-model="form.subject_id"
          class="homework-item__full"
          placeholder="不指定科目"
          clearable
          :value-on-clear="() => null"
        >
          <el-option
            v-for="o in subjectOptions"
            :key="o.value"
            :label="o.label"
            :value="o.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item
        label="內容"
        required
        :error="titleError"
      >
        <el-input
          v-model="form.title"
          maxlength="100"
          show-word-limit
          placeholder="例如：數學習作 p.12-13"
          @input="titleError = ''"
        />
        <div
          v-if="recentTitles.length"
          class="homework-recent"
        >
          <span class="homework-recent__label">最近使用</span>
          <el-tag
            v-for="t in recentTitles"
            :key="t"
            type="info"
            effect="plain"
            disable-transitions
            role="button"
            tabindex="0"
            @click="applyRecent(t)"
            @keydown.enter.prevent="applyRecent(t)"
            @keydown.space.prevent="applyRecent(t)"
          >
            {{ t }}
          </el-tag>
        </div>
      </el-form-item>
    </el-form>
  </FormDialog>
</template>

<style scoped>
.homework-item__error {
  margin-bottom: 16px;
}

.homework-item__full {
  width: 100%;
}

.homework-recent {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  width: 100%;
  margin-top: 8px;
}

.homework-recent__label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.homework-recent :deep(.el-tag) {
  height: 32px;
  cursor: pointer;
}
</style>
