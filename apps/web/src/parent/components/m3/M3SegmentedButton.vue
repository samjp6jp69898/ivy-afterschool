<script setup lang="ts">
/**
 * M3 單選分段按鈕。role=radiogroup + 每段 role=radio；roving tabindex（只有選中段在 Tab 順序內）。
 * ← → 移到下一個非 disabled 段並立即選取，兩端循環。選中段以 check 取代原圖示。
 */
import { computed, nextTick, useTemplateRef } from 'vue'
import M3Icon from './M3Icon.vue'

export interface SegmentItem {
  value: string
  label: string
  icon?: string
  disabled?: boolean
}

const props = defineProps<{
  modelValue: string
  items: SegmentItem[]
  ariaLabel: string
}>()

const emit = defineEmits<{ 'update:modelValue': [string] }>()

const segments = useTemplateRef<HTMLButtonElement[]>('segments')

// 目前值不在清單內時，讓第一個可用段進入 Tab 順序
const focusIndex = computed(() => {
  const index = props.items.findIndex((item) => item.value === props.modelValue)
  return index >= 0 ? index : props.items.findIndex((item) => !item.disabled)
})

function select(item: SegmentItem): void {
  if (item.disabled) return
  emit('update:modelValue', item.value)
}

function onKeydown(event: KeyboardEvent, index: number): void {
  if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return
  event.preventDefault()
  const step = event.key === 'ArrowRight' ? 1 : -1
  const total = props.items.length
  let next = index
  for (let i = 0; i < total; i += 1) {
    next = (next + step + total) % total
    if (!props.items[next]?.disabled) break
  }
  const target = props.items[next]
  if (!target || target.disabled || next === index) return
  emit('update:modelValue', target.value)
  void nextTick(() => segments.value?.[next]?.focus())
}
</script>

<template>
  <div
    class="m3-segmented"
    role="radiogroup"
    :aria-label="ariaLabel"
    :style="{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }"
  >
    <button
      v-for="(item, index) in items"
      :key="item.value"
      ref="segments"
      type="button"
      role="radio"
      class="m3-segment m3-label-large"
      :class="{ 'is-checked': item.value === modelValue }"
      :aria-checked="item.value === modelValue ? 'true' : 'false'"
      :disabled="item.disabled"
      :tabindex="index === focusIndex ? 0 : -1"
      @click="select(item)"
      @keydown="onKeydown($event, index)"
    >
      <M3Icon
        v-if="item.value === modelValue"
        name="check"
        :size="18"
      />
      <M3Icon
        v-else-if="item.icon"
        :name="item.icon"
        :size="18"
      />
      <span class="m3-segment__label">{{ item.label }}</span>
    </button>
  </div>
</template>

<style scoped>
.m3-segmented {
  display: grid;
  width: 100%;
  height: 48px;
  overflow: hidden;
  border: 1px solid var(--m3-outline);
  border-radius: var(--m3-shape-full);
}

.m3-segment {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-width: 0;
  padding: 0 12px;
  border: none;
  border-left: 1px solid var(--m3-outline);
  background: transparent;
  color: var(--m3-on-surface);
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.m3-segment:first-child {
  border-left: none;
}

.m3-segment.is-checked {
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}

/* state layer */
.m3-segment::before {
  content: '';
  position: absolute;
  inset: 0;
  background: currentColor;
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--m3-dur-short-2) var(--m3-easing-standard);
}

.m3-segment:hover::before {
  opacity: var(--m3-state-hover);
}

.m3-segment:focus-visible::before {
  opacity: var(--m3-state-focus);
}

.m3-segment:active::before {
  opacity: var(--m3-state-pressed);
}

.m3-segment__label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.m3-segment:focus-visible {
  outline: 2px solid var(--m3-primary);
  outline-offset: -4px;
  border-radius: var(--m3-shape-full);
}

.m3-segment:disabled {
  cursor: not-allowed;
}

.m3-segment:disabled > * {
  opacity: 0.38;
}

.m3-segment:disabled::before {
  opacity: 0;
}
</style>
