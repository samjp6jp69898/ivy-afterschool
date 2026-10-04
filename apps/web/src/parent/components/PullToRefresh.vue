<script setup lang="ts">
/**
 * 下拉重新整理。onRefresh 用 function prop（不是 emit）才能 await 到完成再收回指示器。
 * 只在頁面捲到最頂端時啟動；只有真的進入下拉才 preventDefault，往上滑照常捲動。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import M3Icon from './m3/M3Icon.vue'

const props = withDefaults(
  defineProps<{
    onRefresh: () => Promise<unknown>
    threshold?: number
    disabled?: boolean
  }>(),
  { threshold: 64, disabled: false },
)

defineSlots<{ default?: () => unknown }>()

const DAMPING = 0.5
const HOLD_HEIGHT = 56
const RING_LENGTH = 2 * Math.PI * 12
// 超過這段距離才判定手勢方向（在瀏覽器開始捲動之前）
const DIRECTION_SLOP = 8

const rootRef = ref<HTMLElement | null>(null)
const pullDistance = ref(0)
const refreshing = ref(false)
const tracking = ref(false)
let pulling = false
let startX = 0
let startY = 0

const armed = computed(() => !refreshing.value && pullDistance.value >= props.threshold)
const label = computed(() => {
  if (refreshing.value) return '重新整理中…'
  return armed.value ? '放開即可重新整理' : '下拉重新整理'
})
const indicatorStyle = computed(() => ({
  height: `${pullDistance.value}px`,
  opacity: refreshing.value ? 1 : Math.min(1, pullDistance.value / props.threshold),
}))
const contentStyle = computed(() =>
  pullDistance.value > 0 ? { transform: `translateY(${pullDistance.value}px)` } : undefined,
)
const ringDash = computed(() => {
  const progress = refreshing.value ? 0.7 : Math.min(1, pullDistance.value / props.threshold)
  return `${RING_LENGTH * progress} ${RING_LENGTH}`
})

function pageScrollTop(): number {
  return window.scrollY || document.scrollingElement?.scrollTop || 0
}

function cancelGesture(): void {
  tracking.value = false
  pulling = false
  // 重新整理中的 56px 由 runRefresh 收回，手勢中斷不影響
  if (!refreshing.value) pullDistance.value = 0
}

function onTouchStart(event: TouchEvent): void {
  // 第二指按下（多指縮放等）：取消這次下拉
  if (event.touches.length !== 1) {
    if (tracking.value) cancelGesture()
    return
  }
  if (props.disabled || refreshing.value || pageScrollTop() > 0) return
  tracking.value = true
  pulling = false
  startX = event.touches[0]!.clientX
  startY = event.touches[0]!.clientY
}

function onTouchMove(event: TouchEvent): void {
  if (!tracking.value || event.touches.length !== 1) return
  const dx = event.touches[0]!.clientX - startX
  const dy = event.touches[0]!.clientY - startY
  if (!pulling) {
    if (Math.abs(dx) < DIRECTION_SLOP && Math.abs(dy) < DIRECTION_SLOP) return
    // 水平滑動：整個手勢交還給瀏覽器（chip 列等水平捲動）
    if (Math.abs(dx) >= Math.abs(dy)) {
      cancelGesture()
      return
    }
  }
  if (dy <= 0) {
    if (pulling) {
      pulling = false
      pullDistance.value = 0
    }
    return
  }
  // 下拉途中頁面被捲走：取消
  if (pageScrollTop() > 0) {
    cancelGesture()
    return
  }
  pulling = true
  if (event.cancelable) event.preventDefault()
  pullDistance.value = Math.min(props.threshold * 2, dy * DAMPING)
}

function onTouchEnd(event: TouchEvent): void {
  // 還有手指按著（多指）不算放開
  if (!tracking.value || event.touches.length > 0) return
  const shouldRefresh = pulling && pullDistance.value >= props.threshold
  tracking.value = false
  pulling = false
  if (shouldRefresh) void runRefresh()
  else pullDistance.value = 0
}

async function runRefresh(): Promise<void> {
  if (refreshing.value) return
  refreshing.value = true
  pullDistance.value = HOLD_HEIGHT
  try {
    await props.onRefresh()
  } catch {
    // 失敗提示由呼叫端負責，這裡只收回指示器
  } finally {
    refreshing.value = false
    pullDistance.value = 0
  }
}

onMounted(() => {
  const el = rootRef.value
  if (!el) return
  el.addEventListener('touchstart', onTouchStart, { passive: true })
  el.addEventListener('touchmove', onTouchMove, { passive: false })
  el.addEventListener('touchend', onTouchEnd, { passive: true })
  el.addEventListener('touchcancel', cancelGesture, { passive: true })
})

onBeforeUnmount(() => {
  const el = rootRef.value
  if (!el) return
  el.removeEventListener('touchstart', onTouchStart)
  el.removeEventListener('touchmove', onTouchMove)
  el.removeEventListener('touchend', onTouchEnd)
  el.removeEventListener('touchcancel', cancelGesture)
})
</script>

<template>
  <div
    ref="rootRef"
    class="ptr"
    :class="{ 'is-tracking': tracking }"
  >
    <div
      class="ptr__indicator"
      role="status"
      :aria-label="refreshing ? '重新整理中' : undefined"
      :style="indicatorStyle"
    >
      <span
        class="ptr__badge"
        aria-hidden="true"
      >
        <svg
          class="ptr__ring"
          :class="{ 'is-spinning': refreshing }"
          viewBox="0 0 28 28"
        >
          <circle
            cx="14"
            cy="14"
            r="12"
            :stroke-dasharray="ringDash"
          />
        </svg>
        <M3Icon
          v-if="!refreshing"
          class="ptr__arrow"
          :class="{ 'is-armed': armed }"
          name="arrow_downward"
          :size="18"
        />
      </span>
      <span
        class="ptr__text m3-label-large"
        aria-hidden="true"
      >{{ label }}</span>
    </div>
    <div
      class="ptr__content"
      :style="contentStyle"
    >
      <slot />
    </div>
  </div>
</template>

<style scoped>
.ptr {
  position: relative;
}

.ptr.is-tracking {
  user-select: none;
}

.ptr__indicator {
  position: absolute;
  z-index: 1;
  top: 0;
  right: 0;
  left: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  overflow: hidden;
  color: var(--m3-on-surface-variant);
  pointer-events: none;
}

.ptr__content {
  will-change: transform;
}

/* 拖曳中跟手；放開後 200ms 回彈或停到 56px */
.ptr:not(.is-tracking) .ptr__indicator {
  transition:
    height var(--m3-dur-short-4) var(--m3-easing-standard),
    opacity var(--m3-dur-short-4) var(--m3-easing-standard);
}

.ptr:not(.is-tracking) .ptr__content {
  transition: transform var(--m3-dur-short-4) var(--m3-easing-standard);
}

.ptr__badge {
  position: relative;
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-surface-container-high);
  color: var(--m3-primary);
  box-shadow: var(--m3-elev-1);
}

.ptr__ring {
  position: absolute;
  inset: 4px;
  width: 28px;
  height: 28px;
  transform: rotate(-90deg);
}

.ptr__ring circle {
  fill: none;
  stroke: var(--m3-primary);
  stroke-linecap: round;
  stroke-width: 2.5;
}

.ptr__ring.is-spinning {
  animation: ptr-spin 0.9s linear infinite;
}

.ptr__arrow {
  transition: transform var(--m3-dur-short-3) var(--m3-easing-standard);
}

.ptr__arrow.is-armed {
  transform: rotate(180deg);
}

.ptr__text {
  white-space: nowrap;
}

@keyframes ptr-spin {
  from {
    transform: rotate(-90deg);
  }

  to {
    transform: rotate(270deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .ptr:not(.is-tracking) .ptr__indicator,
  .ptr:not(.is-tracking) .ptr__content,
  .ptr__arrow {
    transition: none;
  }

  .ptr__ring.is-spinning {
    animation: none;
  }
}
</style>
