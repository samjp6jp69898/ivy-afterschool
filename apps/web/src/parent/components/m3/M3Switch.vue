<script setup lang="ts">
/**
 * M3 開關。disabled 或 busy 時不 emit；busy 時 thumb 留在原位並轉圈（儲存中）。
 * 鍵盤由 keydown 自行切換，並攔下原生按鈕的 Enter / Space 啟動，避免同一次按鍵切換兩次。
 */
import M3Icon from './M3Icon.vue'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    disabled?: boolean
    label: string
    busy?: boolean
  }>(),
  { disabled: false, busy: false },
)

const emit = defineEmits<{ 'update:modelValue': [boolean] }>()

function toggle(): void {
  if (props.disabled || props.busy) return
  emit('update:modelValue', !props.modelValue)
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== ' ' && event.key !== 'Enter') return
  event.preventDefault()
  if (event.repeat) return
  toggle()
}

function onKeyup(event: KeyboardEvent): void {
  if (event.key === ' ') event.preventDefault()
}
</script>

<template>
  <button
    type="button"
    role="switch"
    class="m3-switch"
    :class="{ 'is-on': modelValue, 'is-busy': busy }"
    :aria-checked="modelValue ? 'true' : 'false'"
    :aria-label="label"
    :aria-busy="busy ? 'true' : undefined"
    :disabled="disabled"
    @click="toggle"
    @keydown="onKeydown"
    @keyup="onKeyup"
  >
    <span class="m3-switch__track">
      <span class="m3-switch__thumb">
        <span
          v-if="busy"
          class="m3-switch__spinner"
          aria-hidden="true"
        />
        <M3Icon
          v-else-if="modelValue"
          name="check"
          :size="16"
        />
      </span>
    </span>
  </button>
</template>

<style scoped>
.m3-switch {
  position: relative;
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 48px;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.m3-switch__track {
  position: relative;
  width: 52px;
  height: 32px;
  border: 2px solid var(--m3-outline);
  border-radius: var(--m3-shape-full);
  background: var(--m3-surface-container-highest);
  transition:
    background-color var(--m3-dur-short-3) var(--m3-easing-standard),
    border-color var(--m3-dur-short-3) var(--m3-easing-standard);
}

.m3-switch__thumb {
  position: absolute;
  top: 14px;
  left: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--m3-outline);
  color: var(--m3-on-primary-container);
  transform: translate(-50%, -50%);
  transition:
    left var(--m3-dur-medium-1) var(--m3-easing-standard),
    width var(--m3-dur-short-2) var(--m3-easing-standard),
    height var(--m3-dur-short-2) var(--m3-easing-standard);
}

/* hover / pressed：thumb 周圍 40px 圓形 state layer */
.m3-switch__thumb::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  width: 40px;
  height: 40px;
  margin: -20px 0 0 -20px;
  border-radius: 50%;
  background: var(--m3-on-surface);
  opacity: 0;
  pointer-events: none;
}

.m3-switch:hover .m3-switch__thumb::before {
  opacity: var(--m3-state-hover);
}

.m3-switch:active .m3-switch__thumb::before {
  opacity: var(--m3-state-pressed);
}

.m3-switch.is-on .m3-switch__track {
  border-color: var(--m3-primary);
  background: var(--m3-primary);
}

.m3-switch.is-on .m3-switch__thumb {
  left: 34px;
  width: 24px;
  height: 24px;
  background: var(--m3-on-primary);
}

.m3-switch.is-on .m3-switch__thumb::before {
  background: var(--m3-primary);
}

.m3-switch:active:not(:disabled, .is-busy) .m3-switch__thumb {
  width: 28px;
  height: 28px;
}

.m3-switch.is-busy {
  cursor: progress;
}

.m3-switch.is-busy .m3-switch__thumb {
  width: 24px;
  height: 24px;
}

.m3-switch.is-busy .m3-switch__thumb::before {
  opacity: 0;
}

.m3-switch__spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--m3-surface-container-highest);
  border-right-color: transparent;
  border-radius: 50%;
  animation: m3-switch-spin 0.8s linear infinite;
}

.m3-switch.is-on .m3-switch__spinner {
  border-color: var(--m3-primary);
  border-right-color: transparent;
}

.m3-switch:focus-visible {
  outline: none;
}

.m3-switch:focus-visible .m3-switch__track {
  outline: 2px solid var(--m3-primary);
  outline-offset: 2px;
}

.m3-switch:disabled {
  cursor: not-allowed;
  opacity: 0.38;
}

.m3-switch:disabled .m3-switch__thumb::before {
  opacity: 0;
}

@keyframes m3-switch-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
