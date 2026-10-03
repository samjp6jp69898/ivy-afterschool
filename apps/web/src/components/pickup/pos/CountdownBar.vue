<script setup lang="ts">
// FRONTEND-196：員工代建接送請求的 5 秒倒數條（純視覺，倒數結束要做什麼由呼叫端負責）。
// 移植 ivy FE:src/components/dismissal/pos/DismissalPosCountdownBar.vue；設計稿：docs/mockups/component-pickup-countdown.html。
// - scaleX + transform-origin left 收縮；掛載先畫剩餘比例，雙層 rAF 後才觸發 transition（單層 rAF 可能直接跳到終值）。
// - 剩餘時間 clamp 在 [0, durationMs]；已過期直接空條、不播動畫。
// - reduced motion：fill 全程滿版，到期時 is-counting → is-done 只變色。
// - 掛載當下計算一次，不監聽 props；重新倒數由呼叫端換 :key。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    startedAt: number
    durationMs?: number
  }>(),
  { durationMs: 5000 },
)

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches === true
}

const remaining = Math.min(props.durationMs, Math.max(0, props.durationMs - (Date.now() - props.startedAt)))
const initialRatio = props.durationMs > 0 ? remaining / props.durationMs : 0

const reducedMotion = ref(false)
const shrinking = ref(false)
const counting = ref(remaining > 0)

let timer: ReturnType<typeof setTimeout> | null = null
let rafOuter: number | null = null
let rafInner: number | null = null

const fillStyle = computed(() => {
  if (reducedMotion.value) return {}
  return {
    transform: `scaleX(${shrinking.value ? 0 : initialRatio})`,
    transitionDuration: `${shrinking.value ? remaining : 0}ms`,
  }
})

onMounted(() => {
  reducedMotion.value = prefersReducedMotion()
  if (remaining <= 0) return

  if (reducedMotion.value) {
    timer = setTimeout(() => {
      counting.value = false
    }, remaining)
    return
  }

  rafOuter = requestAnimationFrame(() => {
    rafOuter = null
    rafInner = requestAnimationFrame(() => {
      rafInner = null
      shrinking.value = true
    })
  })
})

onBeforeUnmount(() => {
  if (timer !== null) clearTimeout(timer)
  if (rafOuter !== null) cancelAnimationFrame(rafOuter)
  if (rafInner !== null) cancelAnimationFrame(rafInner)
})
</script>

<template>
  <div
    class="countdown-bar"
    role="progressbar"
    aria-valuemin="0"
    :aria-valuemax="durationMs"
    :aria-valuenow="remaining"
  >
    <div
      class="countdown-bar__fill"
      data-test="countdown-fill"
      :class="{
        'is-reduced': reducedMotion,
        'is-counting': reducedMotion && counting,
        'is-done': reducedMotion && !counting,
      }"
      :style="fillStyle"
    />
  </div>
</template>

<style scoped>
.countdown-bar {
  height: 6px;
  overflow: hidden;
  background: var(--el-fill-color-dark);
  border-radius: 999px;
}

.countdown-bar__fill {
  width: 100%;
  height: 100%;
  background: var(--el-color-primary);
  border-radius: 999px;
  transform-origin: left;
  transition-timing-function: linear;
  transition-property: transform;
}

.countdown-bar__fill.is-reduced {
  transition: none;
}

.countdown-bar__fill.is-done {
  background: var(--el-border-color-darker);
}

@media (prefers-reduced-motion: reduce) {
  .countdown-bar__fill {
    transition: none;
  }
}
</style>
