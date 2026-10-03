<script setup lang="ts">
// FRONTEND-056：secret 設定欄位（BACKEND-110：送回遮罩值 = 不修改、null = 清除、其他字串 = 新值）。
// 設計稿：docs/mockups/component-settings.html。遮罩值永遠不放進可編輯 input。
// 初始值 = 掛載時的 modelValue；外部把 modelValue 改成「不是自己最後一次 emit 的值」視為父層重設。
import { Lock, Unlock } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: string | null
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{ 'update:modelValue': [value: string | null] }>()

type Mode = 'view' | 'editing' | 'cleared'

const initial = ref<string | null>(props.modelValue)
const mode = ref<Mode>('view')
const draft = ref('')
// undefined = 尚未 emit；用來分辨 v-model 回聲與父層重設
let lastEmitted: string | null | undefined

function emitValue(value: string | null): void {
  lastEmitted = value
  emit('update:modelValue', value)
}

watch(
  () => props.modelValue,
  (value) => {
    if (lastEmitted !== undefined && value === lastEmitted) return
    initial.value = value
    mode.value = 'view'
    draft.value = ''
    lastEmitted = undefined
  },
)

function startEdit(): void {
  draft.value = ''
  mode.value = 'editing'
}

function onInput(value: string): void {
  draft.value = value
  // 清成空字串視同未修改；要清除只能走「清除」
  emitValue(value === '' ? initial.value : value)
}

function cancelEdit(): void {
  draft.value = ''
  mode.value = 'view'
  emitValue(initial.value)
}

async function requestClear(): Promise<void> {
  try {
    await ElMessageBox.confirm('清除後 LINE 推播將無法運作，確定要清除嗎？', '清除設定值', {
      type: 'warning',
      confirmButtonText: '確定',
      cancelButtonText: '取消',
      confirmButtonClass: 'el-button--danger',
    })
  } catch {
    return
  }
  mode.value = 'cleared'
  emitValue(null)
}

function undoClear(): void {
  mode.value = 'view'
  emitValue(initial.value)
}
</script>

<template>
  <div class="secret-input">
    <div
      v-if="mode === 'editing' && !disabled"
      class="secret-input__editor"
    >
      <el-input
        :model-value="draft"
        type="password"
        show-password
        autocomplete="new-password"
        :placeholder="initial ? '輸入新值以取代目前設定' : '輸入設定值'"
        @update:model-value="onInput"
      />
      <el-button @click="cancelEdit">
        取消
      </el-button>
    </div>
    <template v-else-if="mode === 'cleared'">
      <span class="secret-input__status is-cleared">
        <el-icon><Lock /></el-icon>
        <span>已設定（<span class="secret-input__mask">{{ initial }}</span>）</span>
      </span>
      <span class="secret-input__pending">將於儲存後清除</span>
      <el-button
        v-if="!disabled"
        link
        type="primary"
        @click="undoClear"
      >
        復原
      </el-button>
    </template>
    <template v-else-if="initial">
      <span class="secret-input__status">
        <el-icon><Lock /></el-icon>
        <span>已設定（<span class="secret-input__mask">{{ initial }}</span>）</span>
      </span>
      <template v-if="!disabled">
        <el-button
          size="small"
          @click="startEdit"
        >
          修改
        </el-button>
        <el-button
          size="small"
          type="danger"
          plain
          @click="requestClear"
        >
          清除
        </el-button>
      </template>
    </template>
    <template v-else>
      <span class="secret-input__status is-unset">
        <el-icon><Unlock /></el-icon>
        未設定
      </span>
      <el-button
        v-if="!disabled"
        size="small"
        type="primary"
        plain
        @click="startEdit"
      >
        設定
      </el-button>
    </template>
  </div>
</template>

<style scoped>
.secret-input {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  width: 100%;
  min-height: 32px;
}

.secret-input__status {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  font-size: 14px;
  color: var(--el-text-color-regular);
}

.secret-input__status.is-unset {
  color: var(--el-text-color-secondary);
}

.secret-input__status.is-cleared {
  color: var(--el-text-color-placeholder);
  text-decoration: line-through;
}

.secret-input__mask {
  font-family: var(--el-font-family-mono, ui-monospace, monospace);
}

.secret-input__pending {
  font-size: 13px;
  color: var(--el-color-warning-dark-2);
}

.secret-input__editor {
  display: flex;
  flex: 1;
  gap: 8px;
  align-items: center;
  min-width: 260px;
}

.secret-input__editor .el-input {
  flex: 1;
  max-width: 360px;
}

.secret-input :deep(.el-button + .el-button) {
  margin-left: 0;
}
</style>
