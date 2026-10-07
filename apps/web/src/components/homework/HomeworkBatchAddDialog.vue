<script setup lang="ts">
// FRONTEND-156：整班批次新增同一份作業（domain_spec M6；BACKEND-387）。設計稿：docs/mockups/page-homework-board.html。
// - 以 FormDialog md 呈現；送出只 emit submit，由看板呼叫 API、成功後關閉。
// - 對象「全班」不送 student_ids（後端以班級在學學生為準，含今天請假 / 缺席者），送出鈕人數 = 看板上該班全部學生；
//   「指定學生」預設勾選今天不是請假 / 缺席的學生，送出的 student_ids 依清單順序。
// - 科目、內容與「最近使用」同 HomeworkItemDialog（FRONTEND-155），最近使用共用 recentHomeworkTitles。
import { computed, reactive, ref, watch } from 'vue'
import FormDialog from '@/components/common/FormDialog.vue'
import { useFormDirty } from '@/composables/useFormDirty'
import { ATTENDANCE_STATUS_META } from '@/shared/constants/statusLabels'
import type { AttendanceStatus } from '@/shared/types/api'
import { useLookupsStore } from '@/stores/lookups'
import { loadRecentTitles, rememberRecentTitle } from './recentHomeworkTitles'

/** 與 api/homework.ts 的 BoardStudent 相容的子集（本元件只用到這些欄位） */
export interface HomeworkBatchStudent {
  student_id: string
  name: string
  attendance_status: AttendanceStatus | null
}

type Target = 'all' | 'pick'

const props = defineProps<{
  modelValue: boolean
  className: string
  students: HomeworkBatchStudent[]
  loading: boolean
  error: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [body: { subject_id: string | null; title: string; student_ids?: string[] }]
}>()

const lookups = useLookupsStore()

const form = reactive<{ subject_id: string | null; title: string; target: Target; studentIds: string[] }>({
  subject_id: null,
  title: '',
  target: 'all',
  studentIds: [],
})
const errors = reactive({ title: '', target: '' })
const recentTitles = ref<string[]>([])
const { isDirty, markClean } = useFormDirty(form)

/** 今天請假 / 缺席以外（含尚無出勤紀錄）的學生 */
function isPresentToday(s: HomeworkBatchStudent): boolean {
  return s.attendance_status !== 'leave' && s.attendance_status !== 'absent'
}

function presentIds(): string[] {
  return props.students.filter(isPresentToday).map((s) => s.student_id)
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    Object.assign(form, { subject_id: null, title: '', target: 'all', studentIds: presentIds() })
    errors.title = ''
    errors.target = ''
    markClean()
    recentTitles.value = loadRecentTitles()
    lookups.ensure('subjects').catch(() => {
      // 科目可不選：清單載入失敗時下拉是空的，仍可送出
    })
  },
  { immediate: true },
)

watch(
  () => form.studentIds.length,
  () => {
    errors.target = ''
  },
)

const awayCount = computed(() => props.students.filter((s) => !isPresentToday(s)).length)
const count = computed(() => (form.target === 'all' ? props.students.length : form.studentIds.length))

function attendanceNote(s: HomeworkBatchStudent): string {
  return s.attendance_status && s.attendance_status !== 'present'
    ? `（${ATTENDANCE_STATUS_META[s.attendance_status].label}）`
    : ''
}

function applyRecent(title: string): void {
  form.title = title
  errors.title = ''
}

function submit(): void {
  const title = form.title.trim()
  errors.title = title ? '' : '請輸入作業內容'
  errors.target = form.target === 'pick' && form.studentIds.length === 0 ? '請至少選擇一位學生' : ''
  if (errors.title || errors.target) return
  rememberRecentTitle(title)
  if (form.target === 'all') {
    emit('submit', { subject_id: form.subject_id, title })
    return
  }
  const picked = new Set(form.studentIds)
  const studentIds = props.students.filter((s) => picked.has(s.student_id)).map((s) => s.student_id)
  emit('submit', { subject_id: form.subject_id, title, student_ids: studentIds })
}
</script>

<template>
  <FormDialog
    :model-value="modelValue"
    :title="`整班新增作業：${className}`"
    :loading="loading"
    :dirty="isDirty"
    :submit-text="`新增給 ${count} 位學生`"
    @update:model-value="emit('update:modelValue', $event)"
    @submit="submit"
  >
    <el-alert
      v-if="error"
      class="homework-batch__error"
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
          class="homework-batch__full"
          placeholder="不指定科目"
          clearable
          :value-on-clear="() => null"
        >
          <el-option
            v-for="o in lookups.subjectOptions"
            :key="o.value"
            :label="o.label"
            :value="o.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item
        label="內容"
        required
        :error="errors.title"
      >
        <el-input
          v-model="form.title"
          maxlength="100"
          show-word-limit
          placeholder="例如：國語第 5 課生字"
          @input="errors.title = ''"
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
      <el-form-item
        label="對象"
        :error="errors.target"
      >
        <el-radio-group v-model="form.target">
          <el-radio
            value="all"
            size="large"
          >
            全班<span
              v-if="awayCount"
              class="homework-batch__away"
            >（含今天請假 / 缺席 {{ awayCount }} 位）</span>
          </el-radio>
          <el-radio
            value="pick"
            size="large"
          >
            指定學生
          </el-radio>
        </el-radio-group>
      </el-form-item>
      <template v-if="form.target === 'pick'">
        <div class="homework-batch__actions">
          <el-button @click="form.studentIds = students.map((s) => s.student_id)">
            全選
          </el-button>
          <el-button @click="form.studentIds = presentIds()">
            只選今天到班的
          </el-button>
        </div>
        <el-checkbox-group
          v-model="form.studentIds"
          class="homework-batch__students"
        >
          <el-checkbox
            v-for="s in students"
            :key="s.student_id"
            :value="s.student_id"
          >
            {{ s.name }}<span
              v-if="attendanceNote(s)"
              class="homework-batch__away"
            >{{ attendanceNote(s) }}</span>
          </el-checkbox>
        </el-checkbox-group>
      </template>
    </el-form>
  </FormDialog>
</template>

<style scoped>
.homework-batch__error {
  margin-bottom: 16px;
}

.homework-batch__full {
  width: 100%;
}

.homework-batch__away {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.homework-batch__actions {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.homework-batch__actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.homework-batch__students {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 4px 12px;
  max-height: 220px;
  padding: 8px 12px;
  overflow: auto;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}

.homework-batch__students :deep(.el-checkbox) {
  height: 44px;
  margin-right: 0;
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
