<script setup lang="ts">
/**
 * 收件匣一列：點擊 emit open，標已讀與 deep_link 導頁由 NotificationsView 處理。
 * 未讀以粗體標題、primary 圓點（含視覺隱藏「未讀」）、primary 時間與底色表達。
 * 時間以台北日期與 now 比較：同一天 HH:MM、前一天「昨天 HH:MM」、更早 MM/DD；不自動更新。
 */
import { computed } from 'vue'
import type { Notification } from '@/shared/types/api'
import { addDays, formatTime, toTaipeiDate, todayTaipei } from '@/shared/utils/datetime'
import M3Icon from '../m3/M3Icon.vue'

const props = defineProps<{
  notification: Notification
  /** 測試注入；預設現在 */
  now?: Date
}>()

defineEmits<{ open: [n: Notification] }>()

const EVENT_ICONS: [prefix: string, icon: string][] = [
  ['attendance.', 'how_to_reg'],
  ['homework.', 'assignment'],
  ['pickup.', 'directions_walk'],
  ['exam.', 'grading'],
  ['binding.', 'link'],
]

const unread = computed(() => !props.notification.read_at)

const icon = computed(
  () => EVENT_ICONS.find(([prefix]) => props.notification.event.startsWith(prefix))?.[1] ?? 'notifications',
)

const time = computed(() => {
  const created = props.notification.created_at
  const date = toTaipeiDate(created)
  const today = todayTaipei(props.now ?? new Date())
  if (date === today) return formatTime(created)
  if (date === addDays(today, -1)) return `昨天 ${formatTime(created)}`
  return `${date.slice(5, 7)}/${date.slice(8, 10)}`
})
</script>

<template>
  <li
    class="noti-item"
    :class="{ 'is-unread': unread }"
  >
    <button
      type="button"
      class="noti-item__btn"
      @click="$emit('open', notification)"
    >
      <span
        class="noti-item__icon"
        aria-hidden="true"
      >
        <M3Icon :name="icon" />
      </span>
      <span class="noti-item__body">
        <span class="noti-item__head">
          <span class="noti-item__title m3-body-large">{{ notification.title }}</span>
          <span class="noti-item__time m3-label-medium">{{ time }}</span>
          <span
            v-if="unread"
            class="noti-item__dot"
          ><span class="visually-hidden">未讀</span></span>
        </span>
        <span class="noti-item__text m3-body-medium">{{ notification.body }}</span>
      </span>
    </button>
  </li>
</template>

<style scoped>
.noti-item {
  list-style: none;
}

.noti-item + .noti-item {
  border-top: 1px solid var(--m3-outline-variant);
}

.noti-item__btn {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 16px;
  width: 100%;
  min-height: 72px;
  padding: 12px 16px;
  border: none;
  background: transparent;
  color: var(--m3-on-surface);
  font: inherit;
  text-align: left;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

/* M3 state layer */
.noti-item__btn::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--m3-on-surface);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--m3-dur-short-2) var(--m3-easing-standard);
}

.noti-item__btn:hover::before {
  opacity: var(--m3-state-hover);
}

.noti-item__btn:active::before {
  opacity: var(--m3-state-pressed);
}

.noti-item__btn:focus-visible {
  outline: 2px solid var(--m3-primary);
  outline-offset: -2px;
}

.noti-item.is-unread .noti-item__btn {
  background: var(--m3-surface-container-low);
}

.noti-item__icon {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}

.noti-item.is-unread .noti-item__icon {
  background: var(--m3-primary-container);
  color: var(--m3-on-primary-container);
}

.noti-item__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.noti-item__head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.noti-item__title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.noti-item.is-unread .noti-item__title {
  font-weight: 700;
}

.noti-item__time {
  flex: none;
  color: var(--m3-on-surface-variant);
  font-variant-numeric: tabular-nums;
}

.noti-item.is-unread .noti-item__time {
  color: var(--m3-primary);
}

.noti-item__dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-primary);
}

.noti-item__text {
  display: -webkit-box;
  margin: 0;
  overflow: hidden;
  color: var(--m3-on-surface-variant);
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
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
