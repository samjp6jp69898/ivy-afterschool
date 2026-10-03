<script setup lang="ts">
// FRONTEND-039：頁面標題、副標與動作區。移植 ivy FE:src/components/common/PageHeader.vue。
// 設計稿：docs/mockups/component-common.html。actions 沒內容時不渲染容器（缺權限的按鈕由呼叫端直接不傳）。
defineProps<{
  title: string
  subtitle?: string
}>()

defineSlots<{
  default?: () => unknown
  actions?: () => unknown
}>()
</script>

<template>
  <header class="page-header">
    <div class="page-header__body">
      <h1 class="page-header__title">
        {{ title }}
      </h1>
      <p
        v-if="subtitle"
        class="page-header__subtitle"
        data-test="page-subtitle"
      >
        {{ subtitle }}
      </p>
      <div
        v-if="$slots.default"
        class="page-header__extra"
        data-test="page-extra"
      >
        <slot />
      </div>
    </div>
    <div
      v-if="$slots.actions"
      class="page-header__actions"
      data-test="page-actions"
    >
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
}

.page-header__body {
  flex: 1;
  min-width: 0;
}

.page-header__title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  line-height: 28px;
  color: var(--el-text-color-primary);
}

.page-header__subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.page-header__extra {
  margin-top: 8px;
}

.page-header__actions {
  display: flex;
  flex-shrink: 0;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.page-header__actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

/* 窄螢幕：actions 換行到標題下方、靠左，按鈕維持原寬 */
@media (max-width: 767.98px) {
  .page-header {
    flex-wrap: wrap;
  }

  .page-header__body {
    flex-basis: 100%;
  }

  .page-header__actions {
    width: 100%;
  }
}
</style>
