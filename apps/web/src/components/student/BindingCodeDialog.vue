<script setup lang="ts">
// FRONTEND-093：家長綁定碼只顯示一次。移植 ivy FE:src/components/student/GuardianManager.vue 的 bindingCodeVisible dialog
// 與 copyBindingCode；互動同 TempPasswordDialog（FRONTEND-066）。設計稿：docs/mockups/page-students.html。
// 尚未按過「複製」就要關閉（「完成」、X、Esc）→ 先確認；點遮罩不關。關閉當下移除內容，不保留綁定碼字串。
import { Check, DocumentCopy, WarningFilled } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { formatDateTime } from '@/shared/utils/datetime'

const props = defineProps<{
  modelValue: boolean
  /** 8 碼原文，不含分隔線 */
  code: string
  expiresAt: string
  guardianName: string
  studentName: string
}>()

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const copied = ref(false)
const copyFailed = ref(false)

const groupedCode = computed(() => `${props.code.slice(0, 4)}-${props.code.slice(4)}`)

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
    await navigator.clipboard.writeText(props.code)
    copied.value = true
    copyFailed.value = false
  } catch {
    copyFailed.value = true
  }
}

async function requestClose(): Promise<void> {
  if (!copied.value) {
    try {
      await ElMessageBox.confirm('關閉後將無法再次查看此綁定碼，確定已提供給家長了嗎？', '確認關閉', {
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
    title="家長綁定碼"
    width="440px"
    :close-on-click-modal="false"
    :before-close="requestClose"
    append-to-body
  >
    <!-- 關閉當下就移除內容（不等關閉動畫），DOM 不保留綁定碼字串 -->
    <template v-if="modelValue">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        :title="`請 ${guardianName} 在 LINE 開啟家長端，輸入此綁定碼即可綁定 ${studentName}。綁定碼有效至 ${formatDateTime(expiresAt)}，只會顯示這一次。`"
      />
      <div class="binding-code__box">
        <span
          class="binding-code__value"
          data-test="binding-code"
          aria-label="綁定碼"
        >{{ groupedCode }}</span>
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
        class="binding-code__feedback is-success"
        role="status"
      >
        已複製 8 碼綁定碼（不含 -），請交給家長。
      </div>
      <div
        v-else-if="copyFailed"
        class="binding-code__feedback is-error"
        role="alert"
      >
        <el-icon><WarningFilled /></el-icon>
        無法複製，請手動抄寫
      </div>
      <div
        v-else
        class="binding-code__feedback"
      />
    </template>
    <template #footer>
      <el-button
        type="primary"
        @click="requestClose"
      >
        完成
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.binding-code__box {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 14px 16px;
  margin-top: 16px;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
}

.binding-code__value {
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

.binding-code__feedback {
  display: flex;
  gap: 4px;
  align-items: center;
  min-height: 20px;
  margin-top: 8px;
  font-size: 13px;
}

.binding-code__feedback.is-success {
  color: var(--el-color-success);
}

.binding-code__feedback.is-error {
  color: var(--el-color-danger);
}
</style>
