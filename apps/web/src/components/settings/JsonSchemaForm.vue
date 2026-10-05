<script setup lang="ts">
// FRONTEND-055：依後端 json_schema（Pydantic 產生）動態渲染設定表單。
// 參考 ivy FE:src/components/settings/SettingsLineTab.vue 的表單版型，欄位改為 schema 驅動。
// 設計稿：docs/mockups/page-settings-system.html（JsonSchemaForm 段）。
// - 每次變更都 emit 新物件（沿路徑淺拷貝），不修改 props。
// - secret 欄位交給 SecretInput，emit 值原樣、同步寫回，讓 v-model 回聲與 SecretInput 最後一次 emit 相同。
// - 前端驗證錯誤與 errors prop 合併顯示；修改欄位即清除該欄（與所屬 fieldset）的錯誤。
import { computed, ref, watch } from 'vue'
import type { JsonSchema } from '@/api/settings'
import JsonSchemaFields from './JsonSchemaFields.vue'
import {
  getIn,
  pathWithAncestors,
  resolveSchema,
  setIn,
  validateAll,
  validateValue,
  type FieldNode,
} from './jsonSchemaForm'

const props = withDefaults(
  defineProps<{
    schema: JsonSchema
    modelValue: Record<string, unknown>
    secretFields?: string[]
    /** 欄位路徑 → 錯誤訊息（422 details 的 loc 以「.」串接） */
    errors?: Record<string, string>
    readonly?: boolean
  }>(),
  { secretFields: () => [], errors: () => ({}), readonly: false },
)

const emit = defineEmits<{ 'update:modelValue': [value: Record<string, unknown>] }>()

const root = computed(() => resolveSchema(props.schema))

const localErrors = ref<Record<string, string>>({})
// 使用者改過的欄位：errors prop 中對應的訊息不再顯示，直到父層換一份新的 errors
const clearedPropErrors = ref(new Set<string>())

watch(
  () => props.errors,
  () => {
    clearedPropErrors.value = new Set()
  },
)

function errorOf(path: string): string | undefined {
  if (localErrors.value[path]) return localErrors.value[path]
  if (clearedPropErrors.value.has(path)) return undefined
  return props.errors[path] || undefined
}

function clearErrors(segments: string[]): void {
  const paths = pathWithAncestors(segments)
  const next = { ...localErrors.value }
  for (const p of paths) delete next[p]
  localErrors.value = next
  clearedPropErrors.value = new Set([...clearedPropErrors.value, ...paths])
}

function normalize(f: FieldNode, value: unknown): unknown {
  if (value === '' && f.node.nullable) return null
  if (f.node.kind === 'number' && (value === null || value === undefined)) {
    return f.node.nullable ? null : (f.node.schema.minimum ?? 0)
  }
  return value
}

function onInput(f: FieldNode, value: unknown): void {
  clearErrors(f.segments)
  emit('update:modelValue', setIn(props.modelValue, f.segments, normalize(f, value)))
}

function onSecret(f: FieldNode, value: string | null): void {
  clearErrors(f.segments)
  emit('update:modelValue', setIn(props.modelValue, f.segments, value))
}

function onBlur(f: FieldNode): void {
  const msg = validateValue(f.node, getIn(props.modelValue, f.segments))
  const next = { ...localErrors.value }
  if (msg) next[f.path] = msg
  else delete next[f.path]
  localErrors.value = next
}

function onRootToggle(key: string, value: boolean): void {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

/** 驗證全部欄位並顯示錯誤；SettingCard 儲存前呼叫 */
function validate(): boolean {
  const errors = validateAll(root.value.fields ?? [], props.modelValue, props.secretFields)
  localErrors.value = errors
  return Object.keys(errors).length === 0
}

defineExpose({ validate })
</script>

<template>
  <el-form
    class="sf"
    label-position="top"
    @submit.prevent
  >
    <!-- 根 schema 本身是 additionalProperties: boolean（notification.toggles）：開關格線，不顯示欄位標籤 -->
    <div
      v-if="root.kind === 'toggles'"
      class="sf-toggles sf-toggles--root"
    >
      <label
        v-for="k in Object.keys(modelValue)"
        :key="k"
        class="sf-toggle"
        data-test="toggle"
      >
        <span data-test="toggle-label">{{ root.labels?.[k] || k }}</span>
        <el-switch
          :model-value="modelValue[k] as boolean"
          :disabled="readonly"
          @update:model-value="(v) => onRootToggle(k, v as boolean)"
        />
      </label>
    </div>
    <JsonSchemaFields
      v-else-if="root.kind === 'object'"
      :fields="root.fields ?? []"
      :model="modelValue"
      :nested="false"
      :readonly="readonly"
      :secret-fields="secretFields"
      :error-of="errorOf"
      @input="onInput"
      @secret="onSecret"
      @blur="onBlur"
    />
    <div
      v-else
      class="sf-unsupported"
    >
      <span class="sf-unsupported__note">此欄位格式不支援線上編輯</span>
      <code class="sf-unsupported__raw">{{ JSON.stringify(modelValue) }}</code>
    </div>
  </el-form>
</template>

<style scoped>
.sf :deep(.el-form-item) {
  margin-bottom: 20px;
}

.sf :deep(.el-form-item__label) {
  font-weight: 500;
  color: var(--el-text-color-regular);
}

.sf :deep(.sf-desc) {
  width: 100%;
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}

.sf :deep(.sf-input) {
  width: 100%;
  max-width: 420px;
}

.sf :deep(.sf-input--short) {
  width: 180px;
}

.sf :deep(.sf-time) {
  width: 128px;
}

.sf :deep(.sf-unsupported) {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
}

.sf :deep(.sf-unsupported__raw) {
  padding: 6px 10px;
  font-family: var(--el-font-family-mono, ui-monospace, monospace);
  font-size: 12px;
  color: var(--el-text-color-regular);
  word-break: break-all;
  background: var(--el-fill-color-light);
  border-radius: 4px;
}

.sf :deep(.sf-unsupported__note) {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  font-size: 13px;
  color: var(--el-color-warning-dark-2);
}

/* 巢狀 object：fieldset 一列，legend 在左，子欄位橫排（label 在左） */
.sf :deep(.sf-fieldset) {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 20px;
  align-items: center;
  padding: 8px 12px;
  margin: 0;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}

.sf :deep(.sf-fieldset + .sf-fieldset) {
  margin-top: 8px;
}

.sf :deep(.sf-fieldset.is-error) {
  border-color: var(--el-color-danger);
}

.sf :deep(.sf-fieldset__legend) {
  float: left;
  flex: 0 0 48px;
  padding: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.sf :deep(.sf-fieldset .el-form-item) {
  margin-bottom: 0;
}

.sf :deep(.sf-fieldset .el-form-item__label) {
  font-weight: 400;
}

.sf :deep(.sf-fieldset__error) {
  flex-basis: 100%;
  padding-left: 68px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-color-danger);
}

.sf :deep(.sf-label-tip) {
  display: inline-flex;
  gap: 2px;
  align-items: center;
}

.sf :deep(.sf-label-tip .el-icon) {
  color: var(--el-text-color-placeholder);
  cursor: help;
}

/* additionalProperties: boolean（notification.toggles） */
.sf :deep(.sf-toggles) {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 4px 24px;
  width: 100%;
}

.sf .sf-toggles--root {
  margin-bottom: 16px;
}

.sf :deep(.sf-toggle) {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  min-height: 40px;
  padding: 0 4px;
  font-size: 14px;
  color: var(--el-text-color-regular);
  border-bottom: 1px dashed var(--el-border-color-lighter);
}
</style>
