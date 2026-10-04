<script lang="ts">
// 跨實例共享：多個 sheet 同時存在時，只有第一個記錄 body 原值、最後一個還原；
// 堆疊順序決定誰處理 Esc 與焦點鎖（只有最上層）
let scrollLockCount = 0
let scrollLockPrev = ''
const sheetStack: symbol[] = []
</script>

<script setup lang="ts">
/**
 * 家長端 bottom sheet：Teleport 到 body，v-model 開關。
 * 關閉方式：scrim、Esc、標題列關閉鈕、從把手 / 標題列下拖超過 80px（皆需 dismissible）。
 * dismissible=false 時拖曳只給最多 24px 阻尼位移後回彈。開啟時焦點鎖在 sheet 內、
 * 鎖 body 捲動，關閉後焦點回到開啟前的元素。
 */
import { nextTick, onBeforeUnmount, ref, useId, useSlots, watch } from 'vue'
import M3IconButton from './m3/M3IconButton.vue'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title?: string
    dismissible?: boolean
    fullHeight?: boolean
  }>(),
  { title: '', dismissible: true, fullHeight: false },
)

const emit = defineEmits<{ 'update:modelValue': [boolean] }>()

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
const DISMISS_DISTANCE = 80
const LOCKED_MAX_OFFSET = 24

const slots = useSlots()
const titleId = useId()
const self = Symbol('parent-bottom-sheet')
const dialogRef = ref<HTMLElement | null>(null)
const offset = ref(0)
const dragging = ref(false)
let startY = 0
let prevFocus: HTMLElement | null = null
let active = false

function requestClose(): void {
  if (props.dismissible) emit('update:modelValue', false)
}

function isTop(): boolean {
  return sheetStack[sheetStack.length - 1] === self
}

function focusables(dialog: HTMLElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE))
}

function onDocKeydown(event: KeyboardEvent): void {
  if (!isTop()) return
  if (event.key === 'Escape') {
    requestClose()
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

// 焦點因點擊 scrim 或其他方式落到 sheet 外時拉回
function onDocFocusin(event: FocusEvent): void {
  const dialog = dialogRef.value
  if (!isTop() || !dialog || !(event.target instanceof Node) || dialog.contains(event.target)) return
  ;(focusables(dialog)[0] ?? dialog).focus({ preventScroll: true })
}

function startDrag(event: PointerEvent): void {
  dragging.value = true
  startY = event.clientY
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', endDrag)
  window.addEventListener('pointercancel', endDrag)
}

function onHandleDown(event: PointerEvent): void {
  if (event.button === 0 || event.pointerType !== 'mouse') startDrag(event)
}

function onPointerMove(event: PointerEvent): void {
  if (!dragging.value) return
  const dy = Math.max(0, event.clientY - startY)
  offset.value = props.dismissible ? dy : Math.min(LOCKED_MAX_OFFSET, dy / 4)
}

function stopDragListeners(): void {
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', endDrag)
  window.removeEventListener('pointercancel', endDrag)
}

function endDrag(): void {
  stopDragListeners()
  if (!dragging.value) return
  dragging.value = false
  if (props.dismissible && offset.value > DISMISS_DISTANCE) {
    emit('update:modelValue', false)
  } else {
    offset.value = 0
  }
}

function activate(): void {
  if (active) return
  active = true
  prevFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  sheetStack.push(self)
  document.addEventListener('keydown', onDocKeydown)
  document.addEventListener('focusin', onDocFocusin)
  if (scrollLockCount++ === 0) {
    scrollLockPrev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  void nextTick(() => {
    const dialog = dialogRef.value
    if (!dialog) return
    ;(dialog.querySelector<HTMLElement>(FOCUSABLE) ?? dialog).focus({ preventScroll: true })
  })
}

function deactivate(): void {
  if (!active) return
  active = false
  sheetStack.splice(sheetStack.indexOf(self), 1)
  document.removeEventListener('keydown', onDocKeydown)
  document.removeEventListener('focusin', onDocFocusin)
  stopDragListeners()
  offset.value = 0
  dragging.value = false
  if (--scrollLockCount === 0) document.body.style.overflow = scrollLockPrev
  if (prevFocus?.isConnected) prevFocus.focus({ preventScroll: true })
  prevFocus = null
}

watch(
  () => props.modelValue,
  (open) => (open ? activate() : deactivate()),
  { immediate: true },
)

onBeforeUnmount(deactivate)
</script>

<template>
  <Teleport to="body">
    <Transition
      name="sheet"
      :duration="{ enter: 400, leave: 200 }"
    >
      <div
        v-if="modelValue"
        class="sheet-layer"
      >
        <div
          class="sheet-scrim"
          aria-hidden="true"
          @click="requestClose"
        />
        <div
          ref="dialogRef"
          class="sheet"
          :class="{ 'is-full': fullHeight, 'is-dragging': dragging }"
          :style="offset ? { transform: `translateY(${offset}px)` } : undefined"
          role="dialog"
          aria-modal="true"
          :aria-labelledby="titleId"
          tabindex="-1"
        >
          <div
            class="sheet__drag"
            @pointerdown="onHandleDown"
          >
            <div
              class="sheet__handle"
              aria-hidden="true"
            />
            <div
              class="sheet__header"
              :class="{ 'has-close': dismissible }"
            >
              <h2
                :id="titleId"
                class="sheet__title m3-title-large"
              >
                {{ title }}
              </h2>
              <M3IconButton
                v-if="dismissible"
                icon="close"
                label="關閉"
                @pointerdown.stop
                @click="requestClose"
              />
            </div>
          </div>
          <div class="sheet__body">
            <slot />
          </div>
          <div
            v-if="slots.footer"
            class="sheet__footer"
          >
            <slot name="footer" />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* 層級：高於頂部列（5），低於 M3Snackbar（20），接送碼 sheet 上的複製回饋才看得到 */
.sheet-layer {
  position: fixed;
  top: 0;
  right: 0;
  left: 0;
  z-index: 10;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  height: 100dvh;
}

.sheet-scrim {
  position: absolute;
  inset: 0;
  background: var(--m3-scrim);
  opacity: 0.32;
}

.sheet {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  max-height: calc(100% - 56px);
  border-radius: var(--m3-shape-extra-large) var(--m3-shape-extra-large) 0 0;
  background: var(--m3-surface-container-low);
  color: var(--m3-on-surface);
  box-shadow: var(--m3-elev-1);
  outline: none;
  transition: transform var(--m3-dur-short-4) var(--m3-easing-standard);
}

.sheet.is-full {
  height: calc(100% - 56px);
}

.sheet.is-dragging {
  transition: none;
}

.sheet__drag {
  flex: none;
  touch-action: none;
  cursor: grab;
  user-select: none;
}

.sheet.is-dragging .sheet__drag {
  cursor: grabbing;
}

.sheet__handle {
  width: 32px;
  height: 4px;
  margin: 16px auto 8px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-on-surface-variant);
  opacity: 0.4;
}

.sheet__header {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 48px;
  padding: 0 24px 8px;
}

.sheet__header.has-close {
  padding-right: 12px;
}

.sheet__title {
  flex: 1;
  min-width: 0;
  margin: 0;
}

.sheet__body {
  flex: 1 1 auto;
  min-height: 0;
  padding: 0 24px 24px;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.sheet__footer {
  display: flex;
  flex: none;
  gap: 8px;
  padding: 16px 24px calc(16px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--m3-outline-variant);
}

.sheet__footer > * {
  flex: 1 1 0;
}

.sheet-enter-active .sheet {
  transition: transform var(--m3-dur-medium-4) var(--m3-easing-emphasized-decel);
}

.sheet-enter-active .sheet-scrim {
  transition: opacity var(--m3-dur-medium-4) linear;
}

.sheet-leave-active .sheet {
  transition: transform var(--m3-dur-short-4) var(--m3-easing-emphasized-accel) !important;
}

.sheet-leave-active .sheet-scrim {
  transition: opacity var(--m3-dur-short-4) linear;
}

.sheet-enter-from .sheet,
.sheet-leave-to .sheet {
  transform: translateY(100%) !important;
}

.sheet-enter-from .sheet-scrim,
.sheet-leave-to .sheet-scrim {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .sheet,
  .sheet-enter-active .sheet,
  .sheet-leave-active .sheet,
  .sheet-enter-active .sheet-scrim,
  .sheet-leave-active .sheet-scrim {
    transition: none !important;
  }
}
</style>
