<script setup lang="ts">
/**
 * 確認對話框（alertdialog）。元件不自行關閉：只 emit confirm / cancel，由呼叫端把 open 設回 false。
 * 開啟時焦點在取消鈕（避免誤觸破壞性操作）；loading 時確認鈕擋點擊、取消鈕 disabled，
 * Esc 與 scrim 都不 emit cancel（送出中不能取消）。疊在 ParentBottomSheet 之上，共用 overlayStack。
 */
import { nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { isTopOverlay, pushOverlay, removeOverlay } from '../utils/overlayStack'
import M3Button from './m3/M3Button.vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    message?: string
    confirmLabel?: string
    cancelLabel?: string
    destructive?: boolean
    loading?: boolean
  }>(),
  { message: '', confirmLabel: '確定', cancelLabel: '取消', destructive: false, loading: false },
)

const emit = defineEmits<{ confirm: []; cancel: [] }>()

defineSlots<{ default?: () => unknown }>()

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

const self = Symbol('confirm-dialog')
const titleId = useId()
const messageId = useId()
const dialogRef = ref<HTMLElement | null>(null)
const cancelWrapper = ref<HTMLElement | null>(null)
let prevFocus: HTMLElement | null = null
let active = false

function requestCancel(): void {
  if (!props.loading) emit('cancel')
}

function onConfirm(): void {
  if (!props.loading) emit('confirm')
}

function focusables(dialog: HTMLElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE))
}

function onDocKeydown(event: KeyboardEvent): void {
  if (!isTopOverlay(self)) return
  if (event.key === 'Escape') {
    requestCancel()
    return
  }
  if (event.key !== 'Tab') return
  const dialog = dialogRef.value
  if (!dialog) return
  const list = focusables(dialog)
  const first = list[0]
  const last = list[list.length - 1]
  if (!first || !last) {
    event.preventDefault()
    dialog.focus({ preventScroll: true })
    return
  }
  const current = document.activeElement
  if (!(current instanceof Node) || !dialog.contains(current)) {
    event.preventDefault()
    ;(event.shiftKey ? last : first).focus()
  } else if (event.shiftKey && (current === first || current === dialog)) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && current === last) {
    event.preventDefault()
    first.focus()
  }
}

function onDocFocusin(event: FocusEvent): void {
  const dialog = dialogRef.value
  if (!isTopOverlay(self) || !dialog || !(event.target instanceof Node) || dialog.contains(event.target)) return
  ;(focusables(dialog)[0] ?? dialog).focus({ preventScroll: true })
}

function activate(): void {
  if (active) return
  active = true
  prevFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  pushOverlay(self)
  document.addEventListener('keydown', onDocKeydown)
  document.addEventListener('focusin', onDocFocusin)
  void nextTick(() => {
    const cancel = cancelWrapper.value?.querySelector<HTMLElement>('button')
    ;(cancel ?? dialogRef.value)?.focus({ preventScroll: true })
  })
}

function deactivate(): void {
  if (!active) return
  active = false
  removeOverlay(self)
  document.removeEventListener('keydown', onDocKeydown)
  document.removeEventListener('focusin', onDocFocusin)
  if (prevFocus?.isConnected) prevFocus.focus({ preventScroll: true })
  prevFocus = null
}

watch(
  () => props.open,
  (open) => (open ? activate() : deactivate()),
  { immediate: true },
)

onBeforeUnmount(deactivate)
</script>

<template>
  <Teleport to="body">
    <Transition
      name="dialog"
      :duration="{ enter: 200, leave: 150 }"
    >
      <div
        v-if="open"
        class="dialog-layer"
      >
        <div
          class="dialog-scrim"
          aria-hidden="true"
          @click="requestCancel"
        />
        <div
          ref="dialogRef"
          class="dialog"
          role="alertdialog"
          aria-modal="true"
          :aria-labelledby="titleId"
          :aria-describedby="message ? messageId : undefined"
          tabindex="-1"
        >
          <h2
            :id="titleId"
            class="dialog__title m3-headline-small"
          >
            {{ title }}
          </h2>
          <p
            v-if="message"
            :id="messageId"
            class="dialog__message m3-body-medium"
          >
            {{ message }}
          </p>
          <div class="dialog__extra">
            <slot />
          </div>
          <div class="dialog__actions">
            <span
              ref="cancelWrapper"
              class="dialog__action"
            >
              <M3Button
                variant="text"
                :disabled="loading"
                @click="requestCancel"
              >{{ cancelLabel }}</M3Button>
            </span>
            <span class="dialog__action">
              <M3Button
                class="dialog__confirm"
                :class="{ 'is-destructive': destructive }"
                :loading="loading"
                @click="onConfirm"
              >{{ confirmLabel }}</M3Button>
            </span>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* 層級：高於 ParentBottomSheet（10），低於 M3Snackbar（20） */
.dialog-layer {
  position: fixed;
  top: 0;
  right: 0;
  left: 0;
  z-index: 15;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100dvh;
}

.dialog-scrim {
  position: absolute;
  inset: 0;
  background: var(--m3-scrim);
  opacity: 0.32;
}

.dialog {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 16px;
  box-sizing: border-box;
  width: calc(100% - 48px);
  min-width: 280px;
  max-width: 400px;
  max-height: calc(100% - 48px);
  padding: 24px;
  overflow-y: auto;
  border-radius: var(--m3-shape-extra-large);
  background: var(--m3-surface-container-high);
  color: var(--m3-on-surface);
  box-shadow: var(--m3-elev-3);
  outline: none;
}

.dialog__title {
  margin: 0;
  overflow-wrap: anywhere;
}

.dialog__message {
  margin: 0;
  color: var(--m3-on-surface-variant);
  white-space: pre-line;
  overflow-wrap: anywhere;
}

.dialog__extra:empty {
  display: none;
}

/* 放不下一列時換行堆疊（確認在上、取消在下，仍靠右） */
.dialog__actions {
  display: flex;
  flex-wrap: wrap-reverse;
  gap: 8px;
  justify-content: flex-end;
}

.dialog__action {
  display: inline-flex;
  max-width: 100%;
  min-width: 0;
}

.dialog__action :deep(.m3-button) {
  max-width: 100%;
  min-height: 48px;
}

.dialog__action :deep(.m3-button > span:last-child) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dialog__confirm.is-destructive {
  background: var(--m3-error);
  color: var(--m3-on-error);
}

.dialog-enter-active,
.dialog-leave-active {
  transition: opacity 150ms linear;
}

.dialog-enter-active {
  transition-duration: 200ms;
}

.dialog-enter-active .dialog {
  transition: transform 200ms var(--m3-easing-emphasized-decel);
}

.dialog-enter-from,
.dialog-leave-to {
  opacity: 0;
}

.dialog-enter-from .dialog {
  transform: scale(0.92);
}

@media (prefers-reduced-motion: reduce) {
  .dialog-enter-active,
  .dialog-leave-active,
  .dialog-enter-active .dialog {
    transition: none;
  }
}
</style>
