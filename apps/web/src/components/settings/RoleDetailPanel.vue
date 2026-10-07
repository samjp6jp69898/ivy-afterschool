<script setup lang="ts">
// FRONTEND-069：角色詳情（名稱、說明、權限編輯、刪除）。移植 ivy FE:src/components/settings/roles/RoleDetailPanel.vue 的
// 「表單 + PermissionPicker + 儲存 / 刪除」結構；去掉 ApprovalChainEditor、super admin / parent / portal-only flag。
// 設計稿：docs/mockups/page-settings-roles.html（RoleDetailPanel 段）。
// - admin 角色或缺 roles:write：全部唯讀、沒有按鈕。儲存只送有變更的欄位。
// - cannot_grant_permissions 與 last_role_manager / last_staff_manager 顯示在表單頂端可關閉的 alert（要對照勾選清單修改），
//   其他錯誤 ElMessage。
// - 刪除只給非系統角色；仍有啟用員工時停用並以 tooltip 說明。後端 409 role_in_use 的人數含停用帳號。
import { Delete, InfoFilled, Lock } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { deleteRole, updateRole, type PermissionCatalog, type Role, type RoleUpdateBody } from '@/api/roles'
import PermissionPicker from '@/components/settings/PermissionPicker.vue'
import { useConfirmDelete } from '@/composables/useConfirmDelete'
import { useFormDirty } from '@/composables/useFormDirty'
import { PERMISSIONS } from '@/constants/permissions'
import { ApiError, isApiError } from '@/shared/types/api'
import { errorCode, errorMessage } from '@/shared/utils/errorMessage'
import { useAuthStore } from '@/stores/auth'
import { usePermission } from '@/utils/permission'

const props = defineProps<{
  role: Role
  catalog: PermissionCatalog
}>()

const emit = defineEmits<{
  saved: [role: Role]
  deleted: [roleId: string]
}>()

interface RoleForm {
  name: string
  description: string
  permissions: string[]
}

/** 這兩類錯誤要對照勾選清單修改：顯示在表單頂端 */
const MANAGER_CONFLICTS = new Set(['last_role_manager', 'last_staff_manager'])

const auth = useAuthStore()
const { can } = usePermission()
const canWrite = computed(() => can(PERMISSIONS.ROLES_WRITE))

/** 最後一次從父層或儲存回應拿到的角色 */
const current = ref<Role>(props.role)
const isAdmin = computed(() => current.value.code === 'admin')
const editable = computed(() => canWrite.value && !isAdmin.value)

function formOf(role: Role): RoleForm {
  return {
    name: role.name,
    description: role.description ?? '',
    // admin 的 permissions 是 ['*']：顯示展開後的全部碼
    permissions: [...(role.code === 'admin' ? role.effective_permissions : role.permissions)].sort(),
  }
}

const form = reactive<RoleForm>(formOf(props.role))
const nameError = ref('')
const alertMessage = ref('')
const saving = ref(false)
const { isDirty, markClean } = useFormDirty(form)

function resetTo(role: Role): void {
  current.value = role
  Object.assign(form, formOf(role))
  nameError.value = ''
  alertMessage.value = ''
  markClean()
}

watch(() => props.role, resetTo)

const permissionLabels = computed(
  () => new Map(props.catalog.groups.flatMap((g) => g.permissions.map((p) => [p.code, p.label] as const))),
)

function changedFields(): RoleUpdateBody {
  const base = formOf(current.value)
  const body: RoleUpdateBody = {}
  const name = form.name.trim()
  if (name !== base.name) body.name = name
  const description = form.description.trim() || null
  if (description !== (current.value.description ?? null)) body.description = description
  const permissions = [...form.permissions].sort()
  if (permissions.join() !== base.permissions.join()) body.permissions = permissions
  return body
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

async function save(): Promise<void> {
  nameError.value = form.name.trim() ? '' : '請輸入角色名稱'
  if (nameError.value) return
  const body = changedFields()
  if (!Object.keys(body).length) return
  saving.value = true
  alertMessage.value = ''
  try {
    const saved = await updateRole(current.value.id, body)
    resetTo(saved)
    ElMessage.success('角色已更新')
    emit('saved', saved)
  } catch (err) {
    const code = errorCode(err)
    if (code === 'cannot_grant_permissions') alertMessage.value = cannotGrantMessage(err)
    else if (code !== null && MANAGER_CONFLICTS.has(code)) alertMessage.value = errorMessage(err, '儲存失敗，請稍後再試')
    else ElMessage.error(errorMessage(err, '儲存失敗，請稍後再試'))
  } finally {
    saving.value = false
  }
}

/** 409 role_in_use 的人數含停用帳號（RoleOut.staff_count 只算啟用）：改寫訊息交給 useConfirmDelete 顯示 */
function explainRoleInUse(err: unknown): unknown {
  if (!isApiError(err) || err.code !== 'role_in_use') return err
  const count = (err.details as { staff_count?: unknown } | null)?.staff_count
  if (typeof count !== 'number') return err
  return new ApiError(
    err.status,
    err.code,
    `仍有 ${count} 位員工（含已停用的帳號）使用此角色，請先調整他們的角色`,
    err.details,
  )
}

const { confirmDelete, deleting } = useConfirmDelete<Role>({
  request: (role) =>
    deleteRole(role.id).catch((err: unknown) => {
      throw explainRoleInUse(err)
    }),
  confirmMessage: (role) => `確定要刪除角色「${role.name}」嗎？`,
  onSuccess: (role) => emit('deleted', role.id),
})

defineExpose({ isDirty })
</script>

<template>
  <div class="role-detail">
    <header class="role-detail__head">
      <div class="role-detail__titles">
        <h2 class="role-detail__name">
          {{ current.name }}
          <el-tag
            v-if="current.is_system"
            size="small"
            type="info"
            disable-transitions
          >
            系統角色
          </el-tag>
          <el-tag
            v-if="isDirty"
            size="small"
            type="warning"
            disable-transitions
          >
            未儲存
          </el-tag>
        </h2>
        <div class="role-detail__sub">
          {{ current.staff_count }} 位員工使用・{{ current.effective_permissions.length }} 項權限
        </div>
      </div>
      <span
        v-if="!canWrite"
        class="role-detail__readonly"
      ><el-icon><Lock /></el-icon>你只有檢視權限</span>
      <el-tooltip
        v-else-if="!current.is_system"
        :disabled="current.staff_count === 0"
        :content="`仍有 ${current.staff_count} 位員工使用此角色，請先調整他們的角色`"
        placement="bottom-end"
      >
        <span class="role-detail__delete">
          <el-button
            type="danger"
            plain
            :icon="Delete"
            :disabled="current.staff_count > 0"
            :loading="deleting"
            @click="confirmDelete(current)"
          >
            刪除角色
          </el-button>
        </span>
      </el-tooltip>
    </header>

    <div class="role-detail__body">
      <el-alert
        v-if="isAdmin"
        class="role-detail__notice"
        type="info"
        :closable="false"
        show-icon
        title="管理員角色擁有全部權限（含未來新增），不可修改"
      />
      <el-alert
        v-if="alertMessage"
        class="role-detail__alert"
        type="error"
        show-icon
        :title="alertMessage"
        @close="alertMessage = ''"
      />
      <el-form
        label-position="top"
        @submit.prevent
      >
        <el-form-item label="代碼">
          <span class="role-detail__code">{{ current.code }}</span>
        </el-form-item>
        <el-form-item
          label="名稱"
          :required="editable"
          :error="nameError"
        >
          <el-input
            v-model="form.name"
            class="role-detail__name-input"
            maxlength="50"
            show-word-limit
            :disabled="!editable || saving"
            @input="nameError = ''"
          />
        </el-form-item>
        <el-form-item label="說明">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="2"
            maxlength="200"
            show-word-limit
            :disabled="!editable || saving"
            :placeholder="editable ? '這個角色負責的工作，方便日後指派' : ''"
          />
        </el-form-item>
        <el-form-item label="權限">
          <PermissionPicker
            v-model="form.permissions"
            :catalog="catalog"
            :grantable="auth.permissions"
            :disabled="!editable || saving"
          />
        </el-form-item>
      </el-form>
    </div>

    <footer
      v-if="editable"
      class="role-detail__foot"
    >
      <span class="role-detail__hint"><el-icon><InfoFilled /></el-icon>權限變更會在員工下一次操作時立即生效</span>
      <el-button
        type="primary"
        :disabled="!isDirty"
        :loading="saving"
        @click="save"
      >
        儲存
      </el-button>
    </footer>
  </div>
</template>

<style scoped>
.role-detail__head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}

.role-detail__titles {
  flex: 1;
  min-width: 0;
}

.role-detail__name {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  line-height: 26px;
}

.role-detail__sub {
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.role-detail__readonly {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  font-size: 12px;
  line-height: 26px;
  color: var(--el-text-color-secondary);
}

.role-detail__body {
  padding: 16px 20px 4px;
}

.role-detail__body :deep(.el-form-item) {
  margin-bottom: 18px;
}

.role-detail__notice,
.role-detail__alert {
  margin-bottom: 16px;
}

.role-detail__code {
  font-family: var(--el-font-family-mono, ui-monospace, monospace);
  font-size: 14px;
  color: var(--el-text-color-regular);
}

.role-detail__name-input {
  max-width: 360px;
}

.role-detail__foot {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 12px 20px;
  border-top: 1px solid var(--el-border-color-extra-light);
}

.role-detail__hint {
  display: inline-flex;
  flex: 1;
  gap: 4px;
  align-items: center;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
