<script setup lang="ts">
// FRONTEND-092：新增 / 編輯監護人。
// 移植 ivy FE:src/components/student/GuardianManager.vue 的表單規則（姓名必填、電話格式）；欄位改為 GuardianInput。
// 設計稿：docs/mockups/page-students.html（GuardianFormDialog 段）。
// 新增送完整 body；編輯只送變更欄位。422 對應到欄位，其他錯誤（含 409 student_archived）以 ElMessage 顯示後端訊息。
import { ElMessage } from 'element-plus'
import { reactive, ref, watch } from 'vue'
import { createGuardian, updateGuardian, type Guardian, type GuardianInput } from '@/api/guardians'
import FormDialog from '@/components/common/FormDialog.vue'
import { useFormDirty } from '@/composables/useFormDirty'
import { GUARDIAN_RELATION_LABELS } from '@/shared/constants/statusLabels'
import type { GuardianRelation } from '@/shared/types/api'
import { errorMessage, validationFieldErrors } from '@/shared/utils/errorMessage'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    studentId: string
    guardian?: Guardian
    hasOtherPrimary: boolean
  }>(),
  { guardian: undefined },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  saved: [guardian: Guardian]
}>()

interface GuardianForm {
  name: string
  relation: GuardianRelation | null
  phone: string
  is_primary: boolean
  can_pickup: boolean
  receives_notifications: boolean
}

type FieldKey = 'name' | 'relation' | 'phone'

const PHONE_RE = /^[\d\-+() ]{7,20}$/
const RELATION_OPTIONS = Object.entries(GUARDIAN_RELATION_LABELS).map(([value, label]) => ({
  value: value as GuardianRelation,
  label,
}))

function initialForm(): GuardianForm {
  const g = props.guardian
  return {
    name: g?.name ?? '',
    relation: g?.relation ?? null,
    phone: g?.phone ?? '',
    is_primary: g?.is_primary ?? false,
    can_pickup: g?.can_pickup ?? true,
    receives_notifications: g?.receives_notifications ?? true,
  }
}

const form = reactive<GuardianForm>(initialForm())
const errors = reactive<Record<FieldKey, string>>({ name: '', relation: '', phone: '' })
const loading = ref(false)
const { isDirty, markClean } = useFormDirty(form)

function clearErrors(): void {
  errors.name = ''
  errors.relation = ''
  errors.phone = ''
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    Object.assign(form, initialForm())
    clearErrors()
    markClean()
  },
  { immediate: true },
)

function validateField(key: FieldKey): void {
  if (key === 'name') errors.name = form.name.trim() ? '' : '請輸入姓名'
  if (key === 'relation') errors.relation = form.relation ? '' : '請選擇關係'
  if (key === 'phone') {
    const phone = form.phone.trim()
    errors.phone = phone && !PHONE_RE.test(phone) ? '電話格式不正確' : ''
  }
}

function validate(): boolean {
  validateField('name')
  validateField('relation')
  validateField('phone')
  return !errors.name && !errors.relation && !errors.phone
}

function toInput(): GuardianInput {
  return {
    name: form.name.trim(),
    relation: form.relation as GuardianRelation,
    phone: form.phone.trim() || null,
    is_primary: form.is_primary,
    can_pickup: form.can_pickup,
    receives_notifications: form.receives_notifications,
  }
}

/** 與原監護人比較，只留有變更的欄位 */
function changedFields(g: Guardian, input: GuardianInput): Partial<GuardianInput> {
  const out: Partial<GuardianInput> = {}
  const keys = Object.keys(input) as (keyof GuardianInput)[]
  for (const key of keys) {
    if (input[key] !== (g[key] ?? null)) Object.assign(out, { [key]: input[key] })
  }
  return out
}

async function submit(): Promise<void> {
  if (!validate()) return
  const input = toInput()
  const g = props.guardian
  const body = g ? changedFields(g, input) : input
  if (g && Object.keys(body).length === 0) {
    emit('update:modelValue', false)
    return
  }
  loading.value = true
  try {
    const saved = g ? await updateGuardian(g.id, body) : await createGuardian(props.studentId, input)
    ElMessage.success('已儲存')
    emit('saved', saved)
    emit('update:modelValue', false)
  } catch (err) {
    const fieldErrors = validationFieldErrors(err)
    const mapped = (['name', 'relation', 'phone'] as FieldKey[]).filter((k) => fieldErrors[k])
    for (const k of mapped) errors[k] = fieldErrors[k]!
    if (!mapped.length) ElMessage.error(errorMessage(err, '儲存失敗，請稍後再試'))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <FormDialog
    :model-value="modelValue"
    :title="guardian ? '編輯監護人' : '新增監護人'"
    :loading="loading"
    :dirty="isDirty"
    @update:model-value="(v) => emit('update:modelValue', v)"
    @submit="submit"
  >
    <el-form
      label-position="top"
      :disabled="loading"
      @submit.prevent
    >
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item
            label="姓名"
            required
            :error="errors.name"
          >
            <el-input
              v-model="form.name"
              maxlength="30"
              @input="errors.name = ''"
              @blur="validateField('name')"
            />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item
            label="關係"
            required
            :error="errors.relation"
          >
            <el-select
              v-model="form.relation"
              placeholder="選擇關係"
              @change="errors.relation = ''"
            >
              <el-option
                v-for="o in RELATION_OPTIONS"
                :key="o.value"
                :label="o.label"
                :value="o.value"
              />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="24">
          <el-form-item
            label="電話"
            :error="errors.phone"
          >
            <el-input
              v-model="form.phone"
              placeholder="例如：0912-000-123"
              @input="errors.phone = ''"
              @blur="validateField('phone')"
            />
          </el-form-item>
        </el-col>
      </el-row>
      <el-form-item label="主要聯絡人">
        <div class="guardian-form__field">
          <el-switch v-model="form.is_primary" />
          <el-alert
            v-if="form.is_primary && hasOtherPrimary"
            class="guardian-form__alert"
            type="warning"
            :closable="false"
            show-icon
            title="將取代原本的主要聯絡人"
          />
        </div>
      </el-form-item>
      <el-form-item label="可接送">
        <el-switch v-model="form.can_pickup" />
      </el-form-item>
      <el-form-item label="收通知">
        <div class="guardian-form__field">
          <el-switch v-model="form.receives_notifications" />
          <div class="form-hint">
            已綁定家長帳號時，會收到出勤、作業、接送與成績通知
          </div>
        </div>
      </el-form-item>
    </el-form>
  </FormDialog>
</template>

<style scoped>
.guardian-form__field {
  width: 100%;
}

.guardian-form__alert {
  margin-top: 8px;
}

.form-hint {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}
</style>
