<script setup lang="ts">
// FRONTEND-238：已發布考試修改分數時，選擇是否重新通知家長（domain_spec M8）。
// 設計稿：docs/mockups/component-exams.html。
// 選「通知家長」/「不通知」→ emit choose 並關閉；Esc 或 X → 只關閉、不 emit choose（格子維持未儲存）。點遮罩不關。
defineProps<{
  modelValue: boolean
  pendingCount: number
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  choose: [notify: boolean]
}>()

function choose(notify: boolean): void {
  emit('choose', notify)
  emit('update:modelValue', false)
}

// Esc / X：由父層把 modelValue 改為 false 來關閉（受控），不等 Element Plus 關閉動畫結束才通知
function dismiss(): void {
  emit('update:modelValue', false)
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="修改已發布的成績"
    width="440px"
    :close-on-click-modal="false"
    :before-close="dismiss"
    append-to-body
  >
    <p class="renotify__lead">
      此考試已發布，修改分數會記錄於稽核紀錄。是否在修改後通知家長分數已更新？
    </p>
    <p class="renotify__count">
      目前有 {{ pendingCount }} 格等待儲存
    </p>
    <p class="renotify__hint">
      本次編輯期間都會套用此選擇，可在工具列切換。
    </p>
    <template #footer>
      <el-button @click="choose(false)">
        不通知
      </el-button>
      <el-button
        type="primary"
        @click="choose(true)"
      >
        通知家長
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.renotify__lead {
  margin: 0;
  line-height: 1.7;
  color: var(--el-text-color-regular);
}

.renotify__count {
  margin: 12px 0 0;
  font-weight: 600;
  color: var(--el-color-warning-dark-2);
}

.renotify__hint {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
