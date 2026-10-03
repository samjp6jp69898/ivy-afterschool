<script setup lang="ts">
// FRONTEND-043：統計卡。移植 ivy FE:src/components/common/StatCard.vue 的 label / value / 語意色；新增 to、loading、clickable。
// 設計稿：docs/mockups/component-common-extra.html。
// - to 有值：整張卡為連結（role=link），點擊 / Enter 導頁。
// - 沒有 to 但 clickable：role=button + aria-pressed（篩選 chip），點擊 / Enter / Space emit click。
// - 其餘為靜態卡：沒有 role、tabindex、hover，點擊不 emit（例如缺權限的儀表板卡）。
import { ArrowRight } from '@element-plus/icons-vue'
import { computed } from 'vue'
import { useRouter, type RouteLocationRaw } from 'vue-router'
import type { Tone } from '@/shared/constants/statusLabels'

const props = withDefaults(
  defineProps<{
    label: string
    value: string | number
    tone?: Tone
    hint?: string
    to?: RouteLocationRaw
    loading?: boolean
    active?: boolean
    clickable?: boolean
  }>(),
  { tone: 'info', hint: '', to: undefined, loading: false, active: false, clickable: false },
)

const emit = defineEmits<{ click: [] }>()

const router = useRouter()

const isLink = computed(() => props.to !== undefined)
const isButton = computed(() => !isLink.value && props.clickable)
const interactive = computed(() => isLink.value || isButton.value)

function activate(): void {
  if (props.to !== undefined) {
    void router.push(props.to)
  } else if (props.clickable) {
    emit('click')
  }
}

function onKeydown(e: KeyboardEvent): void {
  if (!interactive.value) return
  // 連結只回應 Enter（同原生 <a>）；按鈕回應 Enter 與 Space
  if (e.key === 'Enter' || (isButton.value && e.key === ' ')) {
    e.preventDefault()
    activate()
  }
}
</script>

<template>
  <div
    class="stat-card"
    :class="[`tone-${tone}`, { 'is-interactive': interactive, 'is-active': isButton && active }]"
    :role="isLink ? 'link' : isButton ? 'button' : undefined"
    :tabindex="interactive ? 0 : undefined"
    :aria-pressed="isButton ? (active ? 'true' : 'false') : undefined"
    :aria-busy="loading ? 'true' : undefined"
    @click="activate"
    @keydown="onKeydown"
  >
    <div class="stat-card__label">
      <span>{{ label }}</span>
      <el-icon
        v-if="isLink"
        class="stat-card__chevron"
        data-test="stat-chevron"
      >
        <ArrowRight />
      </el-icon>
    </div>
    <div
      v-if="loading"
      class="stat-card__skeleton"
      data-test="stat-skeleton"
    >
      <el-skeleton animated>
        <template #template>
          <el-skeleton-item
            variant="h3"
            class="stat-card__skeleton-bar"
          />
        </template>
      </el-skeleton>
    </div>
    <template v-else>
      <div
        class="stat-card__value"
        data-test="stat-value"
      >
        {{ value }}
      </div>
      <div
        v-if="hint"
        class="stat-card__hint"
      >
        {{ hint }}
      </div>
    </template>
  </div>
</template>

<style scoped>
.stat-card {
  position: relative;
  min-height: 88px;
  padding: 14px 16px;
  text-align: left;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.stat-card__label {
  display: flex;
  gap: 4px;
  align-items: center;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.stat-card__chevron {
  margin-left: auto;
  font-size: 14px;
  color: var(--el-text-color-placeholder);
}

.stat-card__value {
  margin-top: 6px;
  font-size: 28px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
  color: var(--el-text-color-primary);
}

.stat-card__hint {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.stat-card__skeleton {
  margin-top: 10px;
}

.stat-card__skeleton-bar {
  width: 56px;
  height: 24px;
}

/* tone 只改數值顏色；info 代表沒有特殊狀態，不上色 */
.stat-card.tone-success .stat-card__value {
  color: var(--el-color-success-dark-2);
}

.stat-card.tone-warning .stat-card__value {
  color: var(--el-color-warning-dark-2);
}

.stat-card.tone-danger .stat-card__value {
  color: var(--el-color-danger);
}

.stat-card.is-interactive {
  cursor: pointer;
  transition:
    border-color 0.15s,
    box-shadow 0.15s;
}

.stat-card.is-interactive:hover {
  border-color: var(--el-color-primary-light-5);
  box-shadow: var(--el-box-shadow-lighter);
}

.stat-card.is-interactive:hover .stat-card__chevron {
  color: var(--el-color-primary);
}

.stat-card.is-interactive:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}

.stat-card.is-active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary);
  box-shadow: inset 0 0 0 1px var(--el-color-primary);
}

@media (prefers-reduced-motion: reduce) {
  .stat-card.is-interactive {
    transition: none;
  }
}
</style>
