<script setup lang="ts">
/**
 * M3 卡片：filled（預設）/ elevated / outlined。
 * clickable 時為 role=button、可用 Enter / Space 觸發；不可點的卡片沒有任何互動語意。
 */
const props = withDefaults(
  defineProps<{
    variant?: 'elevated' | 'filled' | 'outlined'
    clickable?: boolean
    padding?: 'none' | 'sm' | 'md'
  }>(),
  { variant: 'filled', clickable: false, padding: 'md' },
)

const emit = defineEmits<{ click: [MouseEvent | KeyboardEvent] }>()

defineSlots<{
  default?: () => unknown
  header?: () => unknown
  actions?: () => unknown
}>()

// 卡片內的控制項自己處理 click 與按鍵（使用者裁定：可點卡片內可以放控制項）
const INTERACTIVE_SELECTOR = [
  'button',
  'a[href]',
  'input',
  'select',
  'textarea',
  'label',
  '[role="button"]',
  '[role="switch"]',
  '[role="link"]',
  '[role="checkbox"]',
  '[contenteditable="true"]',
].join(', ')

function fromNestedControl(event: Event): boolean {
  const target = event.target
  if (!(target instanceof Element)) return false
  const control = target.closest(INTERACTIVE_SELECTOR)
  return control !== null && control !== event.currentTarget
}

function onClick(event: MouseEvent): void {
  if (!props.clickable || fromNestedControl(event)) return
  emit('click', event)
}

function onKeydown(event: KeyboardEvent): void {
  if (!props.clickable || (event.key !== 'Enter' && event.key !== ' ')) return
  if (fromNestedControl(event)) return
  // Space 預設會捲動頁面；按住不放的 repeat 也要擋
  event.preventDefault()
  if (event.repeat) return
  emit('click', event)
}
</script>

<template>
  <div
    class="m3-card"
    :class="[`m3-card--${variant}`, `m3-card--pad-${padding}`, { 'is-clickable': clickable }]"
    :role="clickable ? 'button' : undefined"
    :tabindex="clickable ? 0 : undefined"
    @click="onClick"
    @keydown="onKeydown"
  >
    <div
      v-if="$slots.header"
      class="m3-card__header"
    >
      <slot name="header" />
    </div>
    <slot />
    <div
      v-if="$slots.actions"
      class="m3-card__actions"
    >
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.m3-card {
  position: relative;
  display: block;
  border-radius: var(--m3-shape-medium);
  color: var(--m3-on-surface);
}

.m3-card--pad-none {
  padding: 0;
}

.m3-card--pad-sm {
  padding: 12px;
}

.m3-card--pad-md {
  padding: 16px;
}

.m3-card--filled {
  background: var(--m3-surface-container-highest);
}

.m3-card--elevated {
  background: var(--m3-surface-container-low);
  box-shadow: var(--m3-elev-1);
}

.m3-card--outlined {
  background: var(--m3-surface);
  border: 1px solid var(--m3-outline-variant);
}

.m3-card.is-clickable {
  min-height: 44px;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

/* State layer：hover 0.08 / focus 0.12 / pressed 0.12 */
.m3-card.is-clickable::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: var(--m3-on-surface);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--m3-dur-short-2) var(--m3-easing-standard);
}

.m3-card.is-clickable:hover::before {
  opacity: var(--m3-state-hover);
}

.m3-card.is-clickable:focus-visible::before {
  opacity: var(--m3-state-focus);
}

.m3-card.is-clickable:active::before {
  opacity: var(--m3-state-pressed);
}

.m3-card.is-clickable:focus-visible {
  outline: 2px solid var(--m3-primary);
  outline-offset: 2px;
}

.m3-card__header {
  margin-bottom: 8px;
}

.m3-card__actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
</style>
