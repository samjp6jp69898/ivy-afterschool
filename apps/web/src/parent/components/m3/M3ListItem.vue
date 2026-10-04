<script setup lang="ts">
/**
 * M3 清單列。根元素固定為 li（listitem 語意），互動元素放在 li 內：
 * to 有值且未 disabled → RouterLink；clickable → div[role=button][tabindex=0]。
 * disabled 的 to 列退成不可導頁的 div[role=link]。aria-disabled 放在互動元素上。
 * 清單外層由呼叫端寫 <ul role="list">。
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import M3Icon from './M3Icon.vue'

const props = withDefaults(
  defineProps<{
    headline: string
    supportingText?: string
    leadingIcon?: string
    trailingIcon?: string
    clickable?: boolean
    disabled?: boolean
    to?: string
  }>(),
  { supportingText: '', leadingIcon: '', trailingIcon: '', clickable: false, disabled: false, to: '' },
)

const emit = defineEmits<{ click: [MouseEvent] }>()

const interactive = computed(() => props.clickable || !!props.to)
const isLink = computed(() => !!props.to && !props.disabled)
// 非 RouterLink 的互動 div 才需要 role / tabindex
const role = computed(() => {
  if (isLink.value || !interactive.value) return undefined
  return props.to ? 'link' : 'button'
})
const tabindex = computed(() => {
  if (isLink.value || !interactive.value) return undefined
  return props.disabled ? -1 : 0
})

function onClick(event: MouseEvent): void {
  if (props.disabled || !interactive.value) return
  emit('click', event)
}

function onKeydown(event: KeyboardEvent): void {
  if (isLink.value || !props.clickable || props.disabled) return
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  emit('click', event as unknown as MouseEvent)
}
</script>

<template>
  <li
    class="m3-list-item"
    :class="{ 'is-two-line': !!supportingText, 'is-disabled': disabled }"
  >
    <component
      :is="isLink ? RouterLink : 'div'"
      :to="isLink ? to : undefined"
      class="m3-list-item__inner"
      :class="{ 'is-interactive': interactive }"
      :role="role"
      :tabindex="tabindex"
      :aria-disabled="interactive && disabled ? 'true' : undefined"
      @click="onClick"
      @keydown="onKeydown"
    >
      <span
        v-if="$slots.leading || leadingIcon"
        class="m3-list-item__leading"
        :class="{ 'is-icon': !$slots.leading }"
      >
        <slot name="leading">
          <M3Icon :name="leadingIcon" />
        </slot>
      </span>
      <span class="m3-list-item__content">
        <span class="m3-list-item__headline m3-body-large">{{ headline }}</span>
        <span
          v-if="supportingText"
          class="m3-list-item__supporting m3-body-medium"
        >{{ supportingText }}</span>
      </span>
      <span
        v-if="$slots.trailing || trailingIcon"
        class="m3-list-item__trailing"
      >
        <slot name="trailing">
          <M3Icon :name="trailingIcon" />
        </slot>
      </span>
    </component>
  </li>
</template>

<style scoped>
.m3-list-item {
  list-style: none;
}

.m3-list-item__inner {
  position: relative;
  display: flex;
  align-items: center;
  gap: 16px;
  min-height: 56px;
  padding: 8px 16px;
  color: inherit;
  text-decoration: none;
  outline: none;
  -webkit-tap-highlight-color: transparent;
}

.m3-list-item.is-two-line .m3-list-item__inner {
  min-height: 72px;
}

.m3-list-item__inner.is-interactive {
  cursor: pointer;
}

/* state layer：只有可互動的列 */
.m3-list-item__inner.is-interactive::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--m3-on-surface);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--m3-dur-short-2) var(--m3-easing-standard);
}

.m3-list-item__inner.is-interactive:hover::before {
  opacity: var(--m3-state-hover);
}

.m3-list-item__inner.is-interactive:focus-visible::before {
  opacity: var(--m3-state-focus);
}

.m3-list-item__inner.is-interactive:active::before {
  opacity: var(--m3-state-pressed);
}

.m3-list-item__inner.is-interactive:focus-visible {
  outline: 2px solid var(--m3-primary);
  outline-offset: -2px;
}

.m3-list-item__leading {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  color: var(--m3-on-surface-variant);
}

.m3-list-item__leading.is-icon {
  width: 40px;
  height: 40px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}

.m3-list-item__content {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.m3-list-item__headline {
  overflow: hidden;
  color: var(--m3-on-surface);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.m3-list-item__supporting {
  display: -webkit-box;
  overflow: hidden;
  color: var(--m3-on-surface-variant);
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.m3-list-item__trailing {
  display: inline-flex;
  flex: none;
  align-items: center;
  color: var(--m3-on-surface-variant);
}

.m3-list-item.is-disabled {
  opacity: 0.38;
}

.m3-list-item.is-disabled .m3-list-item__inner {
  cursor: not-allowed;
}

.m3-list-item.is-disabled .m3-list-item__inner::before {
  opacity: 0 !important;
}
</style>
