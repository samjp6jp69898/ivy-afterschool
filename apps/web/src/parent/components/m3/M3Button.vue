<script setup lang="ts">
/**
 * M3 按鈕。loading 以 aria-disabled + aria-busy 擋點擊（保留焦點、不淡化）；disabled 用原生屬性。
 * 轉圈取代圖示位置，沒有圖示時也補上。
 */
import M3Icon from './M3Icon.vue'

const props = withDefaults(
  defineProps<{
    variant?: 'filled' | 'tonal' | 'outlined' | 'text'
    type?: 'button' | 'submit' | 'reset'
    disabled?: boolean
    loading?: boolean
    block?: boolean
    icon?: string
  }>(),
  { variant: 'filled', type: 'button', disabled: false, loading: false, block: false, icon: '' },
)

const emit = defineEmits<{ click: [MouseEvent] }>()

function onClick(event: MouseEvent): void {
  if (props.disabled || props.loading) return
  emit('click', event)
}
</script>

<template>
  <button
    :type="type"
    class="m3-button m3-state m3-label-large"
    :class="[
      `m3-button--${variant}`,
      { 'is-block': block, 'has-icon': !!icon || loading, 'is-loading': loading },
    ]"
    :disabled="disabled"
    :aria-disabled="loading ? 'true' : undefined"
    :aria-busy="loading ? 'true' : undefined"
    @click="onClick"
  >
    <span
      v-if="loading"
      class="m3-spinner"
      aria-hidden="true"
    />
    <M3Icon
      v-else-if="icon"
      :name="icon"
      :size="18"
    />
    <span><slot /></span>
  </button>
</template>

<style scoped>
.m3-button {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 48px;
  min-width: 48px;
  padding: 0 24px;
  border: none;
  border-radius: var(--m3-shape-full);
  cursor: pointer;
  white-space: nowrap;
  -webkit-tap-highlight-color: transparent;
}

.m3-button.has-icon {
  padding-left: 16px;
}

.m3-button--filled {
  background: var(--m3-primary);
  color: var(--m3-on-primary);
}

.m3-button--tonal {
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}

.m3-button--outlined {
  background: transparent;
  color: var(--m3-primary);
  box-shadow: inset 0 0 0 1px var(--m3-outline);
}

.m3-button--text {
  padding: 0 12px;
  background: transparent;
  color: var(--m3-primary);
}

.m3-button--text.has-icon {
  padding-left: 12px;
}

.m3-button.is-block {
  display: flex;
  width: 100%;
}

.m3-button:disabled {
  cursor: not-allowed;
  opacity: 0.38;
}

.m3-button.is-loading {
  cursor: progress;
}

.m3-button:focus-visible {
  outline: 2px solid var(--m3-primary);
  outline-offset: 2px;
}

/* state layer：顏色取 currentColor */
.m3-state::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: currentColor;
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--m3-dur-short-2) var(--m3-easing-standard);
}

.m3-state:hover::before {
  opacity: var(--m3-state-hover);
}

.m3-state:focus-visible::before {
  opacity: var(--m3-state-focus);
}

.m3-state:active::before {
  opacity: var(--m3-state-pressed);
}

.m3-state:disabled::before,
.m3-state[aria-disabled='true']::before {
  opacity: 0 !important;
}

.m3-spinner {
  flex: none;
  width: 18px;
  height: 18px;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: m3-button-spin 0.8s linear infinite;
}

@keyframes m3-button-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
