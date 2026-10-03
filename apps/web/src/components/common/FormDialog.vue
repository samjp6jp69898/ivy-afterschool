<script setup lang="ts">
// FRONTEND-040：統一的表單對話框。移植 ivy FE:src/components/common/FormDialog.vue 的 size 分型、dirty 關閉確認、手機滿版。
// 設計稿：docs/mockups/component-common-extra.html。
// - 送出只 emit submit，由父層成功後關閉。
// - 關閉（取消、X、Esc、點遮罩）走同一條路：loading 中無效；dirty 時先 confirmDiscardChanges。
//   由元件自行 emit update:modelValue(false)，父層改 modelValue 才真的關閉（不等 Element Plus 關閉動畫）。
import { computed, onBeforeUnmount, ref } from 'vue'
import { confirmDiscardChanges } from '@/composables/useFormDirty'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title: string
    size?: 'sm' | 'md' | 'lg'
    loading?: boolean
    submitText?: string
    cancelText?: string
    submitDisabled?: boolean
    dirty?: boolean
    hideFooter?: boolean
    autofocus?: boolean
  }>(),
  {
    size: 'md',
    loading: false,
    submitText: '儲存',
    cancelText: '取消',
    submitDisabled: false,
    dirty: false,
    hideFooter: false,
    autofocus: true,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: []
  closed: []
}>()

const WIDTHS = { sm: '420px', md: '560px', lg: '800px' } as const
const MOBILE_QUERY = '(max-width: 767.98px)'
// 開啟時不搶焦點的欄位：下拉與日期等 picker 一聚焦就會彈出選單
const PICKER_SELECTOR = '.el-select, .el-date-editor, .el-time-picker, .el-cascader, .el-autocomplete'
const EDITABLE_SELECTOR =
  'input:not([disabled]):not([readonly]):not([type=checkbox]):not([type=radio]):not([type=hidden]), textarea:not([disabled]):not([readonly])'

const width = computed(() => WIDTHS[props.size])
const body = ref<HTMLElement | null>(null)

const mediaQuery = typeof window.matchMedia === 'function' ? window.matchMedia(MOBILE_QUERY) : null
const isMobile = ref(mediaQuery?.matches === true)
const onMediaChange = (e: MediaQueryListEvent): void => {
  isMobile.value = e.matches
}
mediaQuery?.addEventListener?.('change', onMediaChange)
onBeforeUnmount(() => mediaQuery?.removeEventListener?.('change', onMediaChange))

async function requestClose(): Promise<void> {
  if (props.loading) return
  if (props.dirty && !(await confirmDiscardChanges())) return
  emit('update:modelValue', false)
}

function focusFirstField(): void {
  if (!props.autofocus || !body.value) return
  const field = Array.from(body.value.querySelectorAll<HTMLElement>(EDITABLE_SELECTOR)).find(
    (el) => !el.closest(PICKER_SELECTOR),
  )
  field?.focus()
}
</script>

<template>
  <el-dialog
    class="form-dialog"
    :model-value="modelValue"
    :title="title"
    :width="width"
    :fullscreen="isMobile"
    :show-close="!loading"
    :before-close="requestClose"
    append-to-body
    @opened="focusFirstField"
    @closed="emit('closed')"
  >
    <div ref="body">
      <slot />
    </div>
    <template
      v-if="!hideFooter"
      #footer
    >
      <div class="form-dialog__footer">
        <el-button
          :disabled="loading"
          @click="requestClose"
        >
          {{ cancelText }}
        </el-button>
        <el-button
          type="primary"
          :loading="loading"
          :disabled="loading || submitDisabled"
          @click="emit('submit')"
        >
          {{ submitText }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.form-dialog__footer {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.form-dialog__footer :deep(.el-button + .el-button) {
  margin-left: 0;
}
</style>

<style>
/*
 * 手機滿版：footer 固定在視窗底部、只有 body 捲動。
 * Element Plus 的 .el-dialog.is-fullscreen 只設 height: 100% 與 overflow: auto（整個 dialog 一起捲動）。
 * dialog teleport 到 body，scoped style 碰不到 .el-dialog，故以 form-dialog 命名空間寫非 scoped 規則。
 */
.el-dialog.form-dialog.is-fullscreen {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.el-dialog.form-dialog.is-fullscreen > .el-dialog__header,
.el-dialog.form-dialog.is-fullscreen > .el-dialog__footer {
  flex: none;
}

.el-dialog.form-dialog.is-fullscreen > .el-dialog__body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}
</style>
