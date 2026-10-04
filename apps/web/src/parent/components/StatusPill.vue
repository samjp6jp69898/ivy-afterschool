<script setup lang="ts">
/**
 * 狀態膠囊（純展示）。tone 與 FRONTEND-005 的 Tone 對應，neutral 為家長端額外的中性樣式；
 * 呼叫端直接傳 statusMeta(...) 的 tone，本元件不寫狀態字串對應。
 * 不可互動、不拿焦點，文字即無障礙名稱；icon 為裝飾（M3Icon 無 label → aria-hidden）。
 */
import M3Icon from './m3/M3Icon.vue'

withDefaults(
  defineProps<{
    label: string
    tone?: 'success' | 'warning' | 'danger' | 'info' | 'neutral'
    icon?: string
  }>(),
  { tone: 'neutral', icon: '' },
)
</script>

<template>
  <span
    class="status-pill m3-label-large"
    :class="[`status-pill--${tone}`, { 'has-icon': !!icon }]"
  >
    <M3Icon
      v-if="icon"
      :name="icon"
      :size="18"
    />
    <span class="status-pill__label">{{ label }}</span>
  </span>
</template>

<style scoped>
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  height: 28px;
  padding: 0 12px;
  border-radius: var(--m3-shape-full);
  white-space: nowrap;
  vertical-align: middle;
}

.status-pill.has-icon {
  padding-left: 8px;
}

.status-pill__label {
  overflow: hidden;
  text-overflow: ellipsis;
}

.status-pill--success {
  background: var(--m3-primary-container);
  color: var(--m3-on-primary-container);
}

.status-pill--warning {
  background: var(--m3-warning-container);
  color: var(--m3-on-warning-container);
}

.status-pill--danger {
  background: var(--m3-error-container);
  color: var(--m3-on-error-container);
}

.status-pill--info {
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}

.status-pill--neutral {
  background: var(--m3-surface-container-high);
  color: var(--m3-on-surface);
}
</style>
