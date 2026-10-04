<script setup lang="ts">
/**
 * M3 圖示按鈕：48×48 點擊區、40×40 圓形視覺。label 必填並寫入 aria-label，
 * 圖示本身是裝飾（M3Icon 無 label → aria-hidden）。
 */
import M3Icon from './M3Icon.vue'

const props = withDefaults(
  defineProps<{
    icon: string
    label: string
    variant?: 'standard' | 'filled' | 'tonal'
    disabled?: boolean
  }>(),
  { variant: 'standard', disabled: false },
)

const emit = defineEmits<{ click: [MouseEvent] }>()

function onClick(event: MouseEvent): void {
  if (props.disabled) return
  emit('click', event)
}
</script>

<template>
  <button
    type="button"
    class="m3-icon-button"
    :class="`m3-icon-button--${variant}`"
    :aria-label="label"
    :disabled="disabled"
    @click="onClick"
  >
    <span class="m3-icon-button__container">
      <M3Icon :name="icon" />
    </span>
  </button>
</template>

<style scoped>
.m3-icon-button {
  position: relative;
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  padding: 0;
  border: none;
  border-radius: var(--m3-shape-full);
  background: transparent;
  color: var(--m3-on-surface-variant);
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.m3-icon-button__container {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--m3-shape-full);
}

/* state layer 只蓋 40px 視覺圓 */
.m3-icon-button__container::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: currentColor;
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--m3-dur-short-2) var(--m3-easing-standard);
}

.m3-icon-button:hover .m3-icon-button__container::before {
  opacity: var(--m3-state-hover);
}

.m3-icon-button:focus-visible .m3-icon-button__container::before {
  opacity: var(--m3-state-focus);
}

.m3-icon-button:active .m3-icon-button__container::before {
  opacity: var(--m3-state-pressed);
}

.m3-icon-button--filled .m3-icon-button__container {
  background: var(--m3-primary);
  color: var(--m3-on-primary);
}

.m3-icon-button--tonal .m3-icon-button__container {
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}

.m3-icon-button:focus-visible {
  outline: 2px solid var(--m3-primary);
  outline-offset: -2px;
}

.m3-icon-button:disabled {
  cursor: not-allowed;
  opacity: 0.38;
}

.m3-icon-button:disabled .m3-icon-button__container::before {
  opacity: 0;
}
</style>
