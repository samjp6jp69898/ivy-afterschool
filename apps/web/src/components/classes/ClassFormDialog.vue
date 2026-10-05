<script setup lang="ts">
// FRONTEND-098：新增 / 編輯班級。
// 移植 ivy FE:src/views/ClassroomView.vue 的 openCreate / openEdit 表單；欄位改為 ClassCreate。
// 設計稿：docs/mockups/page-classes.html（ClassFormDialog 段）。
// 編輯只送變更欄位、沒有變更時「儲存」disabled；寫入成功後讓下拉快取的班級失效。
import { ElMessage } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { createClass, updateClass, type ClassCreate, type ClassItem } from '@/api/classes'
import FormDialog from '@/components/common/FormDialog.vue'
import { useFormDirty } from '@/composables/useFormDirty'
import { useLookupsStore } from '@/stores/lookups'
import { errorCode, errorMessage, validationFieldErrors } from '@/shared/utils/errorMessage'
import { academicYearOptions, GRADE_OPTIONS } from '@/utils/academicYear'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    klass?: ClassItem
    defaultAcademicYear: number
  }>(),
  { klass: undefined },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  saved: [klass: ClassItem]
}>()

type FieldKey = 'name' | 'grade_levels'

const lookups = useLookupsStore()

function initialForm(): ClassCreate {
  const k = props.klass
  return {
    name: k?.name ?? '',
    grade_levels: [...(k?.grade_levels ?? [])],
    academic_year: k?.academic_year ?? props.defaultAcademicYear,
    sort_order: k?.sort_order ?? 0,
  }
}

const form = reactive<ClassCreate>(initialForm())
const errors = reactive<Record<FieldKey, string>>({ name: '', grade_levels: '' })
const loading = ref(false)
const { isDirty, markClean } = useFormDirty(form)

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    Object.assign(form, initialForm())
    errors.name = ''
    errors.grade_levels = ''
    markClean()
  },
  { immediate: true },
)

/** 編輯時班級的學年度可能不在預設範圍內，一併列出 */
const yearOptions = computed(() => {
  const options = academicYearOptions(props.defaultAcademicYear)
  const year = props.klass?.academic_year
  if (year !== undefined && !options.some((o) => o.value === year)) {
    options.push({ label: `${year} 學年度`, value: year })
    options.sort((a, b) => b.value - a.value)
  }
  return options
})

function validate(): boolean {
  errors.name = form.name.trim() ? '' : '請輸入班級名稱'
  errors.grade_levels = form.grade_levels.length ? '' : '請至少選擇一個年級'
  return !errors.name && !errors.grade_levels
}

function toBody(): ClassCreate {
  return {
    name: form.name.trim(),
    grade_levels: [...form.grade_levels].sort((a, b) => a - b),
    academic_year: form.academic_year,
    sort_order: form.sort_order,
  }
}

function changedFields(k: ClassItem, body: ClassCreate): Partial<ClassCreate> {
  const original: ClassCreate = {
    name: k.name,
    grade_levels: [...k.grade_levels].sort((a, b) => a - b),
    academic_year: k.academic_year,
    sort_order: k.sort_order,
  }
  const out: Partial<ClassCreate> = {}
  for (const key of Object.keys(body) as (keyof ClassCreate)[]) {
    if (JSON.stringify(body[key]) !== JSON.stringify(original[key])) Object.assign(out, { [key]: body[key] })
  }
  return out
}

function onSortOrder(v: number | null | undefined): void {
  form.sort_order = v ?? 0
}

async function submit(): Promise<void> {
  if (!validate()) return
  const body = toBody()
  const k = props.klass
  const payload = k ? changedFields(k, body) : body
  if (k && Object.keys(payload).length === 0) return
  loading.value = true
  try {
    const saved = k ? await updateClass(k.id, payload) : await createClass(body)
    lookups.invalidate('classes')
    ElMessage.success(k ? '已更新' : '已新增班級')
    emit('saved', saved)
    emit('update:modelValue', false)
  } catch (err) {
    if (errorCode(err) === 'class_name_taken') {
      errors.name = '此學年度已有同名班級'
      return
    }
    const fieldErrors = validationFieldErrors(err)
    const mapped = (['name', 'grade_levels'] as FieldKey[]).filter((key) => fieldErrors[key])
    for (const key of mapped) errors[key] = fieldErrors[key]!
    if (!mapped.length) ElMessage.error(errorMessage(err, '儲存失敗，請稍後再試'))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <FormDialog
    :model-value="modelValue"
    :title="klass ? '編輯班級' : '新增班級'"
    :loading="loading"
    :dirty="isDirty"
    :submit-disabled="!!klass && !isDirty"
    @update:model-value="(v) => emit('update:modelValue', v)"
    @submit="submit"
  >
    <el-form
      label-position="top"
      :disabled="loading"
      @submit.prevent
    >
      <el-form-item
        label="班級名稱"
        required
        :error="errors.name"
      >
        <el-input
          v-model="form.name"
          maxlength="30"
          show-word-limit
          placeholder="例如 中年級A班"
          @input="errors.name = ''"
        />
      </el-form-item>
      <el-form-item
        label="年級"
        required
        :error="errors.grade_levels"
      >
        <el-checkbox-group
          v-model="form.grade_levels"
          @change="errors.grade_levels = ''"
        >
          <el-checkbox-button
            v-for="g in GRADE_OPTIONS"
            :key="g.value"
            :value="g.value"
          >
            {{ g.label }}
          </el-checkbox-button>
        </el-checkbox-group>
        <div
          v-if="!errors.grade_levels"
          class="form-hint"
        >
          可複選（混齡班）
        </div>
      </el-form-item>
      <div class="cf-grid">
        <el-form-item label="學年度">
          <el-select v-model="form.academic_year">
            <el-option
              v-for="o in yearOptions"
              :key="o.value"
              :label="o.label"
              :value="o.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number
            class="cf-sort"
            :model-value="form.sort_order"
            :min="0"
            :step="1"
            step-strictly
            controls-position="right"
            @update:model-value="onSortOrder"
          />
          <div class="form-hint">
            數字小的排前面，也是班級下拉選單的順序
          </div>
        </el-form-item>
      </div>
    </el-form>
  </FormDialog>
</template>

<style scoped>
.cf-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}

.cf-grid :deep(.el-select),
.cf-sort {
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
