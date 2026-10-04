<script setup lang="ts">
/**
 * M3 底部導覽。80px 高 + safe-area-inset-bottom；active 項目 64×32 膠囊 + filled 圖示。
 * 徽章 aria-hidden，按鈕內另放視覺隱藏文字「，n 則未讀」。點目前分頁照樣 emit select。
 */
import M3Icon from './M3Icon.vue'

export interface NavItem {
  key: string
  label: string
  icon: string
  path: string
  badge?: number
}

defineProps<{
  items: NavItem[]
  currentKey: string
}>()

const emit = defineEmits<{ select: [key: string, item: NavItem] }>()

function badgeLabel(badge: number | undefined): string | null {
  if (!badge || badge < 1) return null
  return badge > 99 ? '99+' : String(badge)
}
</script>

<template>
  <nav
    class="m3-navigation-bar"
    aria-label="主要功能"
  >
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      class="m3-nav-tab"
      :class="{ 'is-active': item.key === currentKey }"
      :aria-current="item.key === currentKey ? 'page' : undefined"
      @click="emit('select', item.key, item)"
    >
      <span class="m3-nav-tab__icon">
        <M3Icon
          :name="item.icon"
          :filled="item.key === currentKey"
        />
        <span
          v-if="badgeLabel(item.badge)"
          class="m3-nav-tab__badge"
          aria-hidden="true"
        >{{ badgeLabel(item.badge) }}</span>
      </span>
      <span class="m3-nav-tab__label m3-label-medium">{{ item.label }}</span>
      <span
        v-if="badgeLabel(item.badge)"
        class="visually-hidden"
      >，{{ item.badge }} 則未讀</span>
    </button>
  </nav>
</template>

<style scoped>
.m3-navigation-bar {
  box-sizing: content-box;
  display: flex;
  flex: none;
  height: 80px;
  padding-bottom: env(safe-area-inset-bottom, 0px);
  background: var(--m3-surface-container);
  color: var(--m3-on-surface-variant);
}

.m3-nav-tab {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-width: 0;
  padding: 12px 0 16px;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  outline: none;
  -webkit-tap-highlight-color: transparent;
}

.m3-nav-tab__icon {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 32px;
  border-radius: var(--m3-shape-full);
  transition: background-color var(--m3-dur-short-3) var(--m3-easing-standard);
}

.m3-nav-tab__icon::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: currentColor;
  opacity: 0;
  pointer-events: none;
}

.m3-nav-tab:hover .m3-nav-tab__icon::before {
  opacity: var(--m3-state-hover);
}

.m3-nav-tab:active .m3-nav-tab__icon::before {
  opacity: var(--m3-state-pressed);
}

.m3-nav-tab:focus-visible .m3-nav-tab__icon {
  outline: 2px solid var(--m3-primary);
  outline-offset: 2px;
}

.m3-nav-tab.is-active .m3-nav-tab__icon {
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}

.m3-nav-tab.is-active .m3-nav-tab__label {
  color: var(--m3-on-surface);
  font-weight: 700;
}

.m3-nav-tab__badge {
  position: absolute;
  top: -2px;
  left: calc(50% + 6px);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-error);
  color: var(--m3-on-error);
  font-size: 11px;
  font-weight: 500;
  line-height: 16px;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  border: 0;
  white-space: nowrap;
  clip: rect(0 0 0 0);
}
</style>
