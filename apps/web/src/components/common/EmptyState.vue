<script setup lang="ts">
// FRONTEND-042：空狀態與錯誤狀態。移植 ivy FE:src/components/common/EmptyState.vue 的 variant 設計（去掉 mobile）。
// 設計稿：docs/mockups/component-common.html。
// - default：FolderOpened icon；error：WarningFilled icon 用 danger 色（標題不變紅），action 放「重試」default 按鈕。
// - inline：表格 #empty 用的精簡版，無 icon。
import { FolderOpened, WarningFilled } from '@element-plus/icons-vue'
import { computed, type Component } from 'vue'

const props = withDefaults(
  defineProps<{
    title: string
    description?: string
    variant?: 'default' | 'error' | 'inline'
    icon?: Component
  }>(),
  { description: '', variant: 'default', icon: undefined },
)

defineSlots<{ action?: () => unknown }>()

const DEFAULT_ICONS = {
  default: { name: 'FolderOpened', component: FolderOpened },
  error: { name: 'WarningFilled', component: WarningFilled },
} as const

const resolvedIcon = computed(() => {
  if (props.icon) {
    return { name: (props.icon as { name?: string }).name ?? 'custom', component: props.icon }
  }
  return props.variant === 'error' ? DEFAULT_ICONS.error : DEFAULT_ICONS.default
})
</script>

<template>
  <div
    class="empty-state"
    :class="{ 'is-error': variant === 'error', 'is-inline': variant === 'inline' }"
    role="status"
  >
    <el-icon
      v-if="variant !== 'inline'"
      class="empty-state__icon"
      data-test="empty-icon"
      :data-icon="resolvedIcon.name"
    >
      <component :is="resolvedIcon.component" />
    </el-icon>
    <p class="empty-state__title">
      {{ title }}
    </p>
    <p
      v-if="description"
      class="empty-state__desc"
      data-test="empty-description"
    >
      {{ description }}
    </p>
    <div
      v-if="$slots.action"
      class="empty-state__action"
      data-test="empty-action"
    >
      <slot name="action" />
    </div>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px 16px;
  text-align: center;
}

.empty-state__icon {
  margin-bottom: 12px;
  font-size: 48px;
  color: var(--el-text-color-placeholder);
}

.empty-state.is-error .empty-state__icon {
  color: var(--el-color-danger);
}

.empty-state__title {
  margin: 0;
  font-size: 16px;
  color: var(--el-text-color-regular);
}

.empty-state__desc {
  max-width: 360px;
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.empty-state__action {
  margin-top: 16px;
}

.empty-state.is-inline {
  padding: 16px;
}

.empty-state.is-inline .empty-state__title {
  font-size: 14px;
}

.empty-state.is-inline .empty-state__action {
  margin-top: 8px;
}
</style>
