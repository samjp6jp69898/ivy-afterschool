<script setup lang="ts">
// FRONTEND-070：新增自訂角色（可從既有角色複製權限）。參考 ivy FE:src/views/settings/SettingsRolesView.vue 的新增角色流程，
// 加上複製權限與 PermissionPicker。設計稿：docs/mockups/page-settings-roles.html（RoleCreateDialog 段）。
// - 代碼 ^[a-z][a-z0-9_]{1,31}$（對齊 BACKEND-076 / DB-003 CHECK），建立後不可修改；名稱 1~50 字。
// - 複製來源：以該角色 effective_permissions 中自己持有（可授出）的碼取代目前勾選，略過的碼數以橘字提示；清除來源不動勾選。
// - 409 role_code_taken 顯示在代碼欄位；403 cannot_grant_permissions 同 FRONTEND-069，以表單頂端可關閉的 alert 列出中文名稱。
import { ElMessage } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { createRole, type PermissionCatalog, type Role, type RoleCreateBody } from '@/api/roles'
import FormDialog from '@/components/common/FormDialog.vue'
import PermissionPicker from '@/components/settings/PermissionPicker.vue'
import { useFormDirty } from '@/composables/useFormDirty'
import { isApiError } from '@/shared/types/api'
import { errorCode, errorMessage, validationFieldErrors } from '@/shared/utils/errorMessage'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{
  modelValue: boolean
  roles: Role[]
  catalog: PermissionCatalog
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  created: [role: Role]
}>()

interface RoleForm {
  code: string
  name: string
  description: string
  copyFrom: string | null
  permissions: string[]
}

type FieldKey = 'code' | 'name'

const CODE_RE = /^[a-z][a-z0-9_]{1,31}$/
const CODE_FORMAT_ERROR = '只能用小寫英文、數字與底線，並以英文字母開頭（2~32 字）'
/** 與 RoleCardsGrid 相同：系統角色依 admin → director → clerk → tutor，其餘依名稱 */
const SYSTEM_ORDER = ['admin', 'director', 'clerk', 'tutor']

const auth = useAuthStore()

function emptyForm(): RoleForm {
  return { code: '', name: '', description: '', copyFrom: null, permissions: [] }
}

const form = reactive<RoleForm>(emptyForm())
const errors = reactive<Record<FieldKey, string>>({ code: '', name: '' })
const skipped = ref(0)
const alertMessage = ref('')
const loading = ref(false)
const { isDirty, markClean } = useFormDirty(form)

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    Object.assign(form, emptyForm())
    errors.code = ''
    errors.name = ''
    skipped.value = 0
    alertMessage.value = ''
    markClean()
  },
  { immediate: true },
)

const copyOptions = computed(() =>
  [...props.roles].sort((a, b) => {
    const ia = SYSTEM_ORDER.indexOf(a.code)
    const ib = SYSTEM_ORDER.indexOf(b.code)
    if (ia !== -1 || ib !== -1) return (ia === -1 ? SYSTEM_ORDER.length : ia) - (ib === -1 ? SYSTEM_ORDER.length : ib)
    return a.name.localeCompare(b.name, 'zh-Hant')
  }),
)

const permissionLabels = computed(
  () => new Map(props.catalog.groups.flatMap((g) => g.permissions.map((p) => [p.code, p.label] as const))),
)

function onCopy(roleId: string | null): void {
  const source = props.roles.find((r) => r.id === roleId)
  if (!source) {
    skipped.value = 0
    return
  }
  const granted = source.effective_permissions.filter((code) => auth.permissions.has(code))
  skipped.value = source.effective_permissions.length - granted.length
  form.permissions = [...granted].sort()
}

function validate(): boolean {
  const code = form.code.trim()
  errors.code = !code ? '請輸入代碼' : CODE_RE.test(code) ? '' : CODE_FORMAT_ERROR
  errors.name = form.name.trim() ? '' : '請輸入角色名稱'
  return !errors.code && !errors.name
}

/** 403 cannot_grant_permissions 的 details.permissions → 中文名稱；沒有清單時用後端訊息 */
function cannotGrantMessage(err: unknown): string {
  const details = isApiError(err) ? (err.details as { permissions?: unknown } | null) : null
  const codes = Array.isArray(details?.permissions)
    ? details.permissions.filter((c): c is string => typeof c === 'string')
    : []
  if (!codes.length) return errorMessage(err, '你沒有部分權限，無法授出')
  return `你沒有以下權限，無法授出：${codes.map((c) => permissionLabels.value.get(c) ?? c).join('、')}`
}

function handleError(err: unknown): void {
  const code = errorCode(err)
  if (code === 'role_code_taken') {
    errors.code = '代碼已被使用'
    return
  }
  if (code === 'cannot_grant_permissions') {
    alertMessage.value = cannotGrantMessage(err)
    return
  }
  const fieldErrors = validationFieldErrors(err)
  const mapped = (['code', 'name'] as FieldKey[]).filter((key) => fieldErrors[key])
  for (const key of mapped) errors[key] = fieldErrors[key]!
  if (!mapped.length) ElMessage.error(errorMessage(err, '建立失敗，請稍後再試'))
}

async function submit(): Promise<void> {
  if (!validate()) return
  const body: RoleCreateBody = {
    code: form.code.trim(),
    name: form.name.trim(),
    description: form.description.trim() || null,
    permissions: [...form.permissions].sort(),
  }
  loading.value = true
  alertMessage.value = ''
  try {
    const role = await createRole(body)
    ElMessage.success('角色已建立')
    emit('created', role)
    emit('update:modelValue', false)
  } catch (err) {
    handleError(err)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <FormDialog
    :model-value="modelValue"
    title="新增角色"
    size="lg"
    submit-text="建立"
    :loading="loading"
    :dirty="isDirty"
    @update:model-value="emit('update:modelValue', $event)"
    @submit="submit"
  >
    <el-alert
      v-if="alertMessage"
      class="role-create__alert"
      type="error"
      show-icon
      :title="alertMessage"
      @close="alertMessage = ''"
    />
    <el-form
      label-position="top"
      :disabled="loading"
      @submit.prevent
    >
      <div class="role-create__row">
        <el-form-item
          label="代碼"
          required
          :error="errors.code"
        >
          <el-input
            v-model="form.code"
            class="role-create__code"
            maxlength="32"
            placeholder="例如 weekend_helper"
            @input="errors.code = ''"
          />
          <div class="form-hint">
            建立後不可修改
          </div>
        </el-form-item>
        <el-form-item
          label="名稱"
          required
          :error="errors.name"
        >
          <el-input
            v-model="form.name"
            maxlength="50"
            show-word-limit
            placeholder="例如 週六支援老師"
            @input="errors.name = ''"
          />
        </el-form-item>
      </div>
      <el-form-item label="說明">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="2"
          maxlength="200"
          show-word-limit
        />
      </el-form-item>
      <el-form-item label="從既有角色複製權限">
        <el-select
          v-model="form.copyFrom"
          class="role-create__copy"
          clearable
          placeholder="不複製，從空白開始"
          :value-on-clear="() => null"
          @change="onCopy"
        >
          <el-option
            v-for="r in copyOptions"
            :key="r.id"
            :label="`${r.name}（${r.effective_permissions.length} 項）`"
            :value="r.id"
          />
        </el-select>
        <div
          v-if="skipped"
          class="role-create__skipped"
        >
          已略過 {{ skipped }} 項你沒有的權限
        </div>
      </el-form-item>
      <el-form-item label="權限">
        <PermissionPicker
          v-model="form.permissions"
          :catalog="catalog"
          :grantable="auth.permissions"
          :disabled="loading"
        />
      </el-form-item>
    </el-form>
  </FormDialog>
</template>

<style scoped>
.role-create__alert {
  margin-bottom: 16px;
}

.role-create__row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}

.role-create__code :deep(.el-input__inner) {
  font-family: var(--el-font-family-mono, ui-monospace, monospace);
}

.role-create__copy {
  width: 280px;
}

.form-hint,
.role-create__skipped {
  width: 100%;
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
}

.form-hint {
  color: var(--el-text-color-secondary);
}

.role-create__skipped {
  color: var(--el-color-warning-dark-2);
}
</style>
