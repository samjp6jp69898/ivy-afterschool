<script setup lang="ts">
// FRONTEND-057：系統設定頁的單一設定卡（表單、儲存、錯誤對應、更新者）。
// 設計稿：docs/mockups/page-settings-system.html（SettingCard 段）。
// - 基準值 = 最後一次從後端拿到的 value；表單與基準不同才可「儲存」「還原」，儲存中整張唯讀。
// - 儲存成功以回傳值重設基準並以 :key 重建 JsonSchemaForm（SecretInput 以掛載時的值為初始值，清除後回 null 時值沒有變化）。
// - 422 invalid_setting_value：loc 以「.」串成欄位路徑交給表單；loc 為空（跨欄位）的訊息列在頂端 alert。
import { Lock } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'
import { updateSetting, type Setting } from '@/api/settings'
import { useFormDirty } from '@/composables/useFormDirty'
import { PERMISSIONS } from '@/constants/permissions'
import { isApiError } from '@/shared/types/api'
import { formatDateTime } from '@/shared/utils/datetime'
import { errorCode, errorMessage } from '@/shared/utils/errorMessage'
import { usePermission } from '@/utils/permission'
import JsonSchemaForm from './JsonSchemaForm.vue'

const props = defineProps<{ setting: Setting }>()

const emit = defineEmits<{ saved: [setting: Setting] }>()

const ALL_FIELDS_MARKED = '請修正標示紅字的欄位後再儲存'

const { can } = usePermission()
const canWrite = computed(() => can(PERMISSIONS.SETTINGS_WRITE))

/** 設定值是 JSON；以 JSON 往返複製（值可能是 reactive Proxy，structuredClone 不能用） */
function clone(value: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(value)) as Record<string, unknown>
}

/** 最後一次從後端拿到的設定（基準值與更新者） */
const current = ref<Setting>(props.setting)
const draft = ref<Record<string, unknown>>(clone(props.setting.value))
const fieldErrors = ref<Record<string, string>>({})
const formErrors = ref<string[]>([])
const saving = ref(false)
const formKey = ref(0)
const form = ref<InstanceType<typeof JsonSchemaForm> | null>(null)
const { isDirty, markClean } = useFormDirty(draft)

const meta = computed(() => {
  const { updated_at: at, updated_by_name: by } = current.value
  if (!at) return '使用預設值'
  return `最後更新：${formatDateTime(at)}${by ? `・${by}` : ''}`
})

/** is_secret 時，最上層的 string 與 nullable string 欄位都交給 SecretInput */
const secretFields = computed(() => {
  if (!props.setting.is_secret) return []
  return Object.entries(props.setting.json_schema.properties ?? {})
    .filter(
      ([, s]) =>
        s.type === 'string' ||
        (s.anyOf?.some((x) => x.type === 'string') === true && s.anyOf.some((x) => x.type === 'null')),
    )
    .map(([key]) => key)
})

function clearErrors(): void {
  fieldErrors.value = {}
  formErrors.value = []
}

function reset(): void {
  draft.value = clone(current.value.value)
  clearErrors()
  formKey.value += 1
}

/** 422 invalid_setting_value 的 details（Pydantic 錯誤清單）→ 欄位錯誤與頂端 alert */
function applyInvalidValue(err: unknown): boolean {
  const details = isApiError(err) && Array.isArray(err.details) ? (err.details as unknown[]) : []
  const fields: Record<string, string> = {}
  const general: string[] = []
  for (const item of details) {
    const { loc, msg } = (item ?? {}) as { loc?: unknown; msg?: unknown }
    if (typeof msg !== 'string' || !msg) continue
    const text = msg.replace(/^Value error, /, '')
    const path = Array.isArray(loc) ? loc.map(String).join('.') : ''
    if (!path) general.push(text)
    else if (!(path in fields)) fields[path] = text
  }
  if (!general.length && !Object.keys(fields).length) return false
  fieldErrors.value = fields
  formErrors.value = general.length ? general : [ALL_FIELDS_MARKED]
  return true
}

async function save(): Promise<void> {
  clearErrors()
  if (!form.value?.validate()) return
  saving.value = true
  try {
    const saved = await updateSetting(props.setting.key, draft.value)
    current.value = saved
    draft.value = clone(saved.value)
    markClean()
    formKey.value += 1
    ElMessage.success(`已儲存「${props.setting.label}」`)
    emit('saved', saved)
  } catch (err) {
    if (errorCode(err) === 'invalid_setting_value' && applyInvalidValue(err)) return
    ElMessage.error(errorMessage(err, '儲存失敗，請稍後再試'))
  } finally {
    saving.value = false
  }
}

defineExpose({ isDirty, reset })
</script>

<template>
  <section
    class="setting-card"
    :data-key="setting.key"
  >
    <header class="setting-card__head">
      <div class="setting-card__titles">
        <h2 class="setting-card__title">
          {{ setting.label }}
          <el-tag
            v-if="isDirty && canWrite"
            size="small"
            type="warning"
            disable-transitions
          >
            未儲存
          </el-tag>
        </h2>
        <div class="setting-card__meta">
          {{ meta }}
        </div>
      </div>
      <span
        v-if="!canWrite"
        class="setting-card__readonly"
      ><el-icon><Lock /></el-icon>你只有檢視權限</span>
    </header>
    <div class="setting-card__body">
      <el-alert
        v-if="formErrors.length"
        class="setting-card__alert"
        type="error"
        :closable="false"
        show-icon
        title="部分欄位不正確"
      >
        <ul>
          <li
            v-for="(message, i) in formErrors"
            :key="i"
          >
            {{ message }}
          </li>
        </ul>
      </el-alert>
      <JsonSchemaForm
        :key="formKey"
        ref="form"
        v-model="draft"
        :schema="setting.json_schema"
        :secret-fields="secretFields"
        :errors="fieldErrors"
        :readonly="!canWrite || saving"
      />
    </div>
    <footer
      v-if="canWrite"
      class="setting-card__foot"
    >
      <el-button
        :disabled="!isDirty || saving"
        @click="reset"
      >
        還原
      </el-button>
      <el-button
        type="primary"
        :disabled="!isDirty"
        :loading="saving"
        @click="save"
      >
        儲存
      </el-button>
    </footer>
  </section>
</template>

<style scoped>
.setting-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.setting-card__head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 14px 20px 12px;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}

.setting-card__titles {
  flex: 1;
  min-width: 0;
}

.setting-card__title {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  line-height: 24px;
}

.setting-card__meta {
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.setting-card__readonly {
  display: inline-flex;
  flex-shrink: 0;
  gap: 4px;
  align-items: center;
  font-size: 12px;
  line-height: 24px;
  color: var(--el-text-color-secondary);
}

.setting-card__body {
  padding: 16px 20px 4px;
}

.setting-card__alert {
  margin-bottom: 16px;
}

.setting-card__alert ul {
  padding-left: 18px;
  margin: 4px 0 0;
}

.setting-card__foot {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  padding: 12px 20px;
  border-top: 1px solid var(--el-border-color-extra-light);
}

.setting-card__foot :deep(.el-button + .el-button) {
  margin-left: 0;
}
</style>
