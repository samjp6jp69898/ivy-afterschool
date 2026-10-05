<script setup lang="ts">
// FRONTEND-055：JsonSchemaForm 的欄位層（遞迴渲染巢狀 object）。只供 JsonSchemaForm 使用。
// 不持有狀態：值由 model 讀取，變更以 input / secret / blur 事件交給 JsonSchemaForm 產生新物件。
import { QuestionFilled, Warning } from '@element-plus/icons-vue'
import SecretInput from './SecretInput.vue'
import { isRequired, type FieldNode } from './jsonSchemaForm'

const props = defineProps<{
  fields: FieldNode[]
  /** 本層的值（巢狀時為父層 model[key]） */
  model: Record<string, unknown>
  nested: boolean
  readonly: boolean
  secretFields: string[]
  errorOf: (path: string) => string | undefined
}>()

const emit = defineEmits<{
  input: [field: FieldNode, value: unknown]
  secret: [field: FieldNode, value: string | null]
  blur: [field: FieldNode]
}>()

function valueOf(f: FieldNode): unknown {
  return props.model[f.key]
}

function objectOf(f: FieldNode): Record<string, unknown> {
  const v = props.model[f.key]
  return v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {}
}

function toggleKeys(f: FieldNode): string[] {
  return Object.keys(objectOf(f))
}

function onToggle(f: FieldNode, key: string, value: boolean): void {
  emit('input', f, { ...objectOf(f), [key]: value })
}

function enumOptions(f: FieldNode): (string | number | boolean)[] {
  return (f.node.schema.enum ?? []) as (string | number | boolean)[]
}

function isSecret(f: FieldNode): boolean {
  return props.secretFields.includes(f.path)
}
</script>

<template>
  <template
    v-for="f in fields"
    :key="f.key"
  >
    <!-- 巢狀 object：fieldset 一列，legend 在左，子欄位橫排 -->
    <fieldset
      v-if="f.node.kind === 'object'"
      class="sf-fieldset"
      :class="{ 'is-error': !!errorOf(f.path) }"
      :data-test="`fieldset-${f.path}`"
    >
      <legend class="sf-fieldset__legend">
        {{ f.label }}
      </legend>
      <JsonSchemaFields
        :fields="f.node.fields ?? []"
        :model="objectOf(f)"
        nested
        :readonly="readonly"
        :secret-fields="secretFields"
        :error-of="errorOf"
        @input="(field, value) => emit('input', field, value)"
        @secret="(field, value) => emit('secret', field, value)"
        @blur="(field) => emit('blur', field)"
      />
      <div
        v-if="errorOf(f.path)"
        class="sf-fieldset__error"
        role="alert"
      >
        {{ errorOf(f.path) }}
      </div>
    </fieldset>

    <el-form-item
      v-else
      :required="isRequired(f.node)"
      :error="errorOf(f.path) ?? ''"
      :label-position="nested ? 'left' : 'top'"
      :data-test="`field-${f.path}`"
    >
      <template #label>
        <span
          v-if="nested && f.node.description"
          class="sf-label-tip"
        >
          {{ f.label }}
          <el-tooltip
            :content="f.node.description"
            placement="top"
          >
            <el-icon
              role="img"
              :aria-label="f.node.description"
              tabindex="0"
              data-test="field-tip"
            ><QuestionFilled /></el-icon>
          </el-tooltip>
        </span>
        <span v-else>{{ f.label }}</span>
      </template>

      <SecretInput
        v-if="isSecret(f)"
        :model-value="(valueOf(f) as string | null) ?? null"
        :disabled="readonly"
        @update:model-value="(v) => emit('secret', f, v)"
      />
      <el-switch
        v-else-if="f.node.kind === 'boolean'"
        :model-value="valueOf(f) as boolean"
        :disabled="readonly"
        @update:model-value="(v) => emit('input', f, v)"
      />
      <el-time-select
        v-else-if="f.node.kind === 'time'"
        class="sf-time"
        :model-value="(valueOf(f) as string | null) ?? undefined"
        start="00:00"
        step="00:05"
        end="23:55"
        :clearable="f.node.nullable"
        :persistent="false"
        :disabled="readonly"
        @update:model-value="(v) => emit('input', f, v)"
      />
      <el-input-number
        v-else-if="f.node.kind === 'number'"
        class="sf-input--short"
        :model-value="(valueOf(f) as number | null) ?? undefined"
        :min="f.node.schema.minimum"
        :max="f.node.schema.maximum"
        :precision="f.node.schema.type === 'integer' ? 0 : undefined"
        controls-position="right"
        :disabled="readonly"
        @update:model-value="(v) => emit('input', f, v)"
      />
      <el-select
        v-else-if="f.node.kind === 'enum'"
        class="sf-input"
        :model-value="valueOf(f)"
        :disabled="readonly"
        @update:model-value="(v) => emit('input', f, v)"
      >
        <el-option
          v-for="o in enumOptions(f)"
          :key="String(o)"
          :label="String(o)"
          :value="o"
        />
      </el-select>
      <el-input
        v-else-if="f.node.kind === 'url'"
        class="sf-input"
        type="url"
        placeholder="https://"
        :model-value="(valueOf(f) as string | null) ?? ''"
        :clearable="f.node.nullable && !readonly"
        :disabled="readonly"
        @update:model-value="(v) => emit('input', f, v)"
        @blur="emit('blur', f)"
      />
      <el-input
        v-else-if="f.node.kind === 'text'"
        class="sf-input"
        :model-value="(valueOf(f) as string | null) ?? ''"
        :maxlength="f.node.schema.maxLength"
        :show-word-limit="!!f.node.schema.maxLength && !readonly"
        :disabled="readonly"
        @update:model-value="(v) => emit('input', f, v)"
        @blur="emit('blur', f)"
      />
      <div
        v-else-if="f.node.kind === 'toggles'"
        class="sf-toggles"
      >
        <label
          v-for="k in toggleKeys(f)"
          :key="k"
          class="sf-toggle"
          data-test="toggle"
        >
          <span data-test="toggle-label">{{ f.node.labels?.[k] || k }}</span>
          <el-switch
            :model-value="objectOf(f)[k] as boolean"
            :disabled="readonly"
            @update:model-value="(v) => onToggle(f, k, v as boolean)"
          />
        </label>
      </div>
      <div
        v-else
        class="sf-unsupported"
      >
        <span class="sf-unsupported__note">
          <el-icon><Warning /></el-icon>此欄位格式不支援線上編輯
        </span>
        <code class="sf-unsupported__raw">{{ JSON.stringify(valueOf(f)) }}</code>
      </div>

      <div
        v-if="!nested && f.node.description"
        class="sf-desc"
        data-test="field-description"
      >
        {{ f.node.description }}
      </div>
    </el-form-item>
  </template>
</template>
