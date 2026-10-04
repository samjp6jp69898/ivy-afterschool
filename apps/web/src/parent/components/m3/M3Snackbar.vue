<script setup lang="ts">
/**
 * 顯示 snackbar store 佇列的第一則（PARENT-024 傳入 items[0]）。
 * info → role=status + aria-live=polite；error → role=alert，訊息前加 filled error 圖示。
 */
import type { SnackbarItem } from '../../stores/snackbar'
import M3Icon from './M3Icon.vue'

const props = defineProps<{ item: SnackbarItem | null }>()

const emit = defineEmits<{ dismiss: [id: number] }>()

// 按鈕帶所屬那一則的 id：出場中的舊按鈕被點到時，props.item 已是下一則，必須忽略
function onAction(ownerId: number): void {
  const current = props.item
  if (!current?.action || current.id !== ownerId) return
  current.action.onClick()
  emit('dismiss', current.id)
}

function onActionClick(event: MouseEvent): void {
  onAction(Number((event.currentTarget as HTMLElement).dataset.snackbarId))
}
</script>

<template>
  <Transition
    name="m3-snackbar"
    mode="out-in"
  >
    <div
      v-if="item"
      :key="item.id"
      class="m3-snackbar"
      :class="`m3-snackbar--${item.tone}`"
      :role="item.tone === 'error' ? 'alert' : 'status'"
      :aria-live="item.tone === 'error' ? undefined : 'polite'"
    >
      <M3Icon
        v-if="item.tone === 'error'"
        class="m3-snackbar__icon"
        name="error"
        filled
      />
      <span class="m3-snackbar__message m3-body-medium">{{ item.message }}</span>
      <button
        v-if="item.action"
        type="button"
        class="m3-snackbar__action m3-label-large"
        :data-snackbar-id="item.id"
        @click="onActionClick"
      >
        {{ item.action.label }}
      </button>
    </div>
  </Transition>
</template>

<style scoped>
/* 底部導覽（80px + safe-area）上方 16px；寬度跟 layout 的 560px 一起置中 */
.m3-snackbar {
  position: fixed;
  z-index: 20;
  right: 16px;
  bottom: calc(96px + env(safe-area-inset-bottom));
  left: 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  max-width: 528px;
  min-height: 48px;
  margin: 0 auto;
  padding: 4px 8px 4px 16px;
  border-radius: var(--m3-shape-extra-small);
  background: var(--m3-inverse-surface);
  color: var(--m3-inverse-on-surface);
  box-shadow: var(--m3-elev-3);
}

.m3-snackbar__icon {
  color: var(--m3-error-container);
}

.m3-snackbar__message {
  display: -webkit-box;
  flex: 1;
  min-width: 0;
  padding: 10px 0;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
}

.m3-snackbar__action {
  position: relative;
  flex: none;
  min-width: 44px;
  min-height: 44px;
  padding: 0 12px;
  border: none;
  border-radius: var(--m3-shape-full);
  background: transparent;
  color: var(--m3-inverse-primary);
  cursor: pointer;
}

.m3-snackbar__action::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: currentColor;
  opacity: 0;
  pointer-events: none;
}

.m3-snackbar__action:hover::before {
  opacity: var(--m3-state-hover);
}

.m3-snackbar__action:active::before {
  opacity: var(--m3-state-pressed);
}

.m3-snackbar__action:focus-visible {
  outline: 2px solid var(--m3-inverse-primary);
  outline-offset: 0;
}

.m3-snackbar-enter-active {
  transition:
    opacity var(--m3-dur-medium-1) var(--m3-easing-emphasized-decel),
    transform var(--m3-dur-medium-1) var(--m3-easing-emphasized-decel);
}

.m3-snackbar-leave-active {
  pointer-events: none;
  transition: opacity var(--m3-dur-short-3) var(--m3-easing-emphasized-accel);
}

.m3-snackbar-enter-from {
  opacity: 0;
  transform: translateY(16px);
}

.m3-snackbar-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .m3-snackbar-enter-active,
  .m3-snackbar-leave-active {
    transition: none;
  }

  .m3-snackbar-enter-from {
    transform: none;
  }
}
</style>
