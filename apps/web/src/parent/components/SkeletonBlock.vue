<script setup lang="ts">
/**
 * 載入骨架。外層 aria-busy + 視覺隱藏「載入中」，色塊全部 aria-hidden；
 * 不加 role=status，同頁多個骨架時才不會重複朗讀。
 */
import { computed, type StyleValue } from 'vue'

const props = withDefaults(
  defineProps<{
    variant?: 'line' | 'card' | 'row'
    count?: number
    width?: string
    height?: string
  }>(),
  { variant: 'line', count: 1, width: undefined, height: undefined },
)

const total = computed(() => Math.max(1, Math.floor(props.count)))

const blockStyle = computed<StyleValue | undefined>(() => {
  if (!props.width && !props.height) return undefined
  return { width: props.width, height: props.height }
})

// 沒指定寬度時最後一行縮短，看起來像一段文字
function lineStyle(index: number): StyleValue | undefined {
  if (!props.width && total.value > 1 && index === total.value) {
    return { width: '60%', height: props.height }
  }
  return blockStyle.value
}
</script>

<template>
  <div
    class="skeleton"
    :class="`skeleton--${variant}`"
    aria-busy="true"
  >
    <span class="skeleton__sr">載入中</span>
    <template v-if="variant === 'card'">
      <div
        v-for="i in total"
        :key="i"
        class="skeleton-card"
        :style="blockStyle"
        aria-hidden="true"
      >
        <span class="sk sk--title" />
        <span class="sk" />
        <span class="sk sk--short" />
      </div>
    </template>
    <template v-else-if="variant === 'row'">
      <div
        v-for="i in total"
        :key="i"
        class="skeleton-row"
        :style="blockStyle"
        aria-hidden="true"
      >
        <span class="sk sk--avatar" />
        <span class="skeleton-row__lines">
          <span class="sk sk--narrow" />
          <span class="sk sk--short sk--small" />
        </span>
      </div>
    </template>
    <template v-else>
      <span
        v-for="i in total"
        :key="i"
        class="sk skeleton-line"
        :style="lineStyle(i)"
        aria-hidden="true"
      />
    </template>
  </div>
</template>

<style scoped>
.skeleton {
  display: block;
}

.skeleton__sr {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}

.sk {
  display: block;
  width: 100%;
  height: 14px;
  border-radius: var(--m3-shape-extra-small);
  background: var(--m3-surface-container-high);
  animation: skeleton-pulse 1.2s ease-in-out infinite;
}

.sk--title {
  width: 40%;
  height: 16px;
  margin-bottom: 12px;
}

.sk--short {
  width: 70%;
}

.sk--narrow {
  width: 30%;
}

.sk--small {
  height: 12px;
}

.sk--avatar {
  flex: none;
  width: 40px;
  height: 40px;
  border-radius: var(--m3-shape-full);
}

.skeleton-line + .skeleton-line {
  margin-top: 8px;
}

.skeleton-card {
  padding: 16px;
  border-radius: var(--m3-shape-medium);
  background: var(--m3-surface-container-low);
}

.skeleton-card + .skeleton-card {
  margin-top: 12px;
}

.skeleton-card .sk + .sk {
  margin-top: 8px;
}

.skeleton-row {
  display: flex;
  align-items: center;
  gap: 16px;
  min-height: 72px;
  padding: 8px 16px;
}

.skeleton-row__lines {
  flex: 1;
  min-width: 0;
}

.skeleton-row__lines .sk + .sk {
  margin-top: 8px;
}

@keyframes skeleton-pulse {
  50% {
    opacity: 0.5;
  }
}

@media (prefers-reduced-motion: reduce) {
  .sk {
    animation: none;
  }
}
</style>
