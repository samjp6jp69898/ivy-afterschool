<script setup lang="ts">
// FRONTEND-066：臨時密碼只顯示一次（建立帳號、重設密碼、重新啟用共用）。
// 設計稿：docs/mockups/component-settings.html。
// 尚未按過「複製」就要關閉（「我已記下」、X、Esc）→ 先確認；點遮罩不關。關閉當下移除內容，不保留密碼字串。
import { Check, DocumentCopy, WarningFilled } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { ref, watch } from 'vue'

const props = defineProps<{
  modelValue: boolean
  username: string
  displayName: string
  tempPassword: string
}>()

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const copied = ref(false)
const copyFailed = ref(false)

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      copied.value = false
      copyFailed.value = false
    }
  },
)

async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.tempPassword)
    copied.value = true
    copyFailed.value = false
  } catch {
    copyFailed.value = true
  }
}

async function requestClose(): Promise<void> {
  if (!copied.value) {
    try {
      await ElMessageBox.confirm('關閉後將無法再次查看此密碼，確定已記下了嗎？', '確認關閉', {
        type: 'warning',
        confirmButtonText: '確定關閉',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
  }
  emit('update:modelValue', false)
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="臨時密碼"
    width="440px"
    :close-on-click-modal="false"
    :before-close="requestClose"
    append-to-body
  >
    <!-- 關閉當下就移除內容（不等關閉動畫），DOM 不保留密碼字串 -->
    <template v-if="modelValue">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        :title="`此密碼只會顯示這一次。${displayName}（帳號 ${username}）首次登入後必須修改密碼。`"
      />
      <div class="temp-pw__box">
        <span
          class="temp-pw__value"
          data-test="temp-password"
          aria-label="臨時密碼"
        >{{ tempPassword }}</span>
        <el-button
          :type="copied ? 'success' : 'primary'"
          :plain="copied"
          :icon="copied ? Check : DocumentCopy"
          data-test="copy-button"
          @click="copy"
        >
          {{ copied ? '已複製' : '複製' }}
        </el-button>
      </div>
      <div
        v-if="copied"
        class="temp-pw__feedback is-success"
        role="status"
      >
        已複製到剪貼簿，請交給本人。
      </div>
      <div
        v-else-if="copyFailed"
        class="temp-pw__feedback is-error"
        role="alert"
      >
        <el-icon><WarningFilled /></el-icon>
        無法複製，請手動抄寫
      </div>
      <div
        v-else
        class="temp-pw__feedback"
      />
    </template>
    <template #footer>
      <el-button
        type="primary"
        @click="requestClose"
      >
        我已記下
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.temp-pw__box {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 14px 16px;
  margin-top: 16px;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
}

.temp-pw__value {
  flex: 1;
  min-width: 0;
  font-family: var(--el-font-family-mono, ui-monospace, monospace);
  font-size: 26px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  letter-spacing: 2px;
  word-break: break-all;
  user-select: all;
}

.temp-pw__feedback {
  display: flex;
  gap: 4px;
  align-items: center;
  min-height: 20px;
  margin-top: 8px;
  font-size: 13px;
}

.temp-pw__feedback.is-success {
  color: var(--el-color-success);
}

.temp-pw__feedback.is-error {
  color: var(--el-color-danger);
}
</style>
