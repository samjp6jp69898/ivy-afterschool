<script setup lang="ts">
/**
 * 空狀態 / 錯誤狀態。沒有 loading：按鈕只 emit action，重新載入期間由頁面改顯示 SkeletonBlock。
 * error 根元素 role="alert"；empty 不加 live role，避免下拉重新整理時重複朗讀。標題用 <p>，不打亂頁面標題層級。
 */
import { computed } from 'vue'
import M3Button from './m3/M3Button.vue'
import M3Icon from './m3/M3Icon.vue'

const props = withDefaults(
  defineProps<{
    variant?: 'empty' | 'error'
    icon?: string
    title: string
    description?: string
    actionLabel?: string
  }>(),
  { variant: 'empty', icon: '', description: '', actionLabel: '' },
)

const emit = defineEmits<{ action: [] }>()

const iconName = computed(() => props.icon || (props.variant === 'error' ? 'error' : 'inbox'))
const buttonLabel = computed(() => props.actionLabel || (props.variant === 'error' ? '重試' : ''))
</script>

<template>
  <div
    class="empty-state"
    :class="variant === 'error' ? 'is-error' : 'is-empty'"
    :role="variant === 'error' ? 'alert' : undefined"
  >
    <M3Icon
      class="empty-state__icon"
      :name="iconName"
      :size="48"
    />
    <p class="empty-state__title m3-title-medium">
      {{ title }}
    </p>
    <p
      v-if="description"
      class="empty-state__description m3-body-medium"
    >
      {{ description }}
    </p>
    <div
      v-if="buttonLabel"
      class="empty-state__action"
    >
      <M3Button
        variant="tonal"
        :icon="variant === 'error' ? 'refresh' : ''"
        @click="emit('action')"
      >
        {{ buttonLabel }}
      </M3Button>
    </div>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 32px 16px;
  text-align: center;
}

.is-empty .empty-state__icon {
  color: var(--m3-outline);
}

.is-error .empty-state__icon {
  color: var(--m3-error);
}

.empty-state__title {
  max-width: 320px;
  margin: 0;
  color: var(--m3-on-surface);
  overflow-wrap: anywhere;
}

.empty-state__description {
  max-width: 280px;
  margin: 0;
  color: var(--m3-on-surface-variant);
  overflow-wrap: anywhere;
}

.empty-state__action {
  margin-top: 8px;
}
</style>
