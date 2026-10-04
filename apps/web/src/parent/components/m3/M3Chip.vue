<script setup lang="ts">
/**
 * M3 Chip（assist / filter）。filter 以 aria-pressed 表示選取，已選時 leading 換成 check；
 * assist 不帶 aria-pressed。選取由呼叫端控制，元件只 emit click。
 * 視覺高 32px，::after 透明延伸補到 48px 點擊高。
 */
import { computed } from 'vue'
import M3Icon from './M3Icon.vue'

const props = withDefaults(
  defineProps<{
    label: string
    variant?: 'assist' | 'filter'
    selected?: boolean
    icon?: string
    disabled?: boolean
  }>(),
  { variant: 'assist', selected: false, icon: '', disabled: false },
)

const emit = defineEmits<{ click: [MouseEvent] }>()

const isFilter = computed(() => props.variant === 'filter')
const isSelected = computed(() => isFilter.value && props.selected)
const leadingIcon = computed(() => (isSelected.value ? 'check' : props.icon))

function onClick(event: MouseEvent): void {
  if (props.disabled) return
  emit('click', event)
}
</script>

<template>
  <button
    type="button"
    class="m3-chip m3-label-large"
    :class="[`m3-chip--${variant}`, { 'is-selected': isSelected, 'has-icon': !!leadingIcon }]"
    :aria-pressed="isFilter ? (selected ? 'true' : 'false') : undefined"
    :disabled="disabled"
    @click="onClick"
  >
    <M3Icon
      v-if="leadingIcon"
      class="m3-chip__icon"
      :name="leadingIcon"
      :size="18"
    />
    <span>{{ label }}</span>
  </button>
</template>

<style scoped>
.m3-chip {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  padding: 0 16px;
  border: 1px solid var(--m3-outline);
  border-radius: var(--m3-shape-small);
  background: transparent;
  color: var(--m3-on-surface-variant);
  cursor: pointer;
  white-space: nowrap;
  -webkit-tap-highlight-color: transparent;
}

.m3-chip--assist {
  color: var(--m3-on-surface);
}

.m3-chip--assist .m3-chip__icon {
  color: var(--m3-primary);
}

.m3-chip.has-icon {
  padding-left: 8px;
}

.m3-chip.is-selected {
  border-color: transparent;
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}

/* state layer */
.m3-chip::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: currentColor;
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--m3-dur-short-2) var(--m3-easing-standard);
}

.m3-chip:hover::before {
  opacity: var(--m3-state-hover);
}

.m3-chip:focus-visible::before {
  opacity: var(--m3-state-focus);
}

.m3-chip:active::before {
  opacity: var(--m3-state-pressed);
}

/* 透明延伸到 48px 點擊高 */
.m3-chip::after {
  content: '';
  position: absolute;
  top: 50%;
  right: 0;
  left: 0;
  height: 48px;
  transform: translateY(-50%);
}

.m3-chip:focus-visible {
  outline: 2px solid var(--m3-primary);
  outline-offset: 2px;
}

.m3-chip:disabled {
  cursor: not-allowed;
  opacity: 0.38;
}

.m3-chip:disabled::before {
  opacity: 0;
}
</style>
