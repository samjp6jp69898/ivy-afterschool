<script setup lang="ts">
// FRONTEND-036：通知面板（純展示 + emit）。移植 ivy FE:src/components/layout/NotificationPanel.vue 的清單樣式，
// 去掉 reminder groups / action items。設計稿：docs/mockups/page-admin-shell.html。
// 「全部 / 未讀」由面板自己記狀態，切換時 emit filter 讓父層重新載入；寬 360、清單最高 420px 內捲。
import { ref } from 'vue'
import { NOTIFICATION_EVENT_LABELS } from '@/shared/constants/statusLabels'
import type { Notification } from '@/shared/types/api'
import { formatDateTime } from '@/shared/utils/datetime'

defineProps<{
  items: Notification[]
  unreadCount: number
  loading: boolean
}>()

const emit = defineEmits<{
  open: [notification: Notification]
  'read-all': []
  filter: [unreadOnly: boolean]
}>()

const unreadOnly = ref(false)

function onFilterChange(value: string | number | boolean | undefined): void {
  unreadOnly.value = value === 'unread'
  emit('filter', unreadOnly.value)
}
</script>

<template>
  <div class="notif">
    <div class="notif__head">
      <span class="notif__title">通知</span>
      <el-segmented
        :model-value="unreadOnly ? 'unread' : 'all'"
        size="small"
        :options="[
          { label: '全部', value: 'all' },
          { label: '未讀', value: 'unread' },
        ]"
        @change="onFilterChange"
      />
      <span class="notif__spacer" />
      <el-button
        link
        type="primary"
        :disabled="unreadCount === 0"
        @click="emit('read-all')"
      >
        全部標為已讀
      </el-button>
    </div>
    <div class="notif__list">
      <template v-if="loading">
        <div
          v-for="i in 3"
          :key="i"
          class="notif__skeleton"
        >
          <el-skeleton
            :rows="1"
            animated
          />
        </div>
      </template>
      <div
        v-else-if="items.length === 0"
        class="notif__empty"
      >
        {{ unreadOnly ? '沒有未讀通知' : '目前沒有通知' }}
      </div>
      <template v-else>
        <button
          v-for="n in items"
          :key="n.id"
          type="button"
          class="notif__row"
          :class="{ 'is-unread': !n.read_at }"
          @click="emit('open', n)"
        >
          <span
            v-if="!n.read_at"
            class="notif__dot"
            data-test="unread-dot"
            aria-label="未讀"
          />
          <div class="notif__row-title">
            {{ n.title }}
          </div>
          <div class="notif__row-body">
            {{ n.body }}
          </div>
          <div class="notif__row-meta">
            <el-tag
              size="small"
              type="info"
              disable-transitions
            >
              {{ NOTIFICATION_EVENT_LABELS[n.event] ?? n.event }}
            </el-tag>
            <span>{{ formatDateTime(n.created_at) }}</span>
          </div>
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* 負 margin 抵銷 popover 的內距，讓表頭與列分隔線貼齊邊緣 */
.notif {
  width: 360px;
  margin: -12px;
}

.notif__head {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 12px 12px 10px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.notif__title {
  font-size: 15px;
  font-weight: 600;
}

.notif__spacer {
  flex: 1;
}

.notif__list {
  max-height: 420px;
  overflow: auto;
}

.notif__row {
  position: relative;
  display: block;
  width: 100%;
  padding: 12px 16px 12px 28px;
  font-family: inherit;
  color: inherit;
  text-align: left;
  cursor: pointer;
  background: var(--el-bg-color);
  border: none;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}

.notif__row:hover,
.notif__row:focus-visible {
  background: var(--el-fill-color-light);
  outline: none;
}

.notif__row.is-unread {
  background: var(--el-color-primary-light-9);
}

.notif__row.is-unread:hover {
  background: var(--el-color-primary-light-8);
}

.notif__dot {
  position: absolute;
  top: 18px;
  left: 12px;
  width: 8px;
  height: 8px;
  background: var(--el-color-primary);
  border-radius: 50%;
}

.notif__row-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.notif__row-body {
  display: -webkit-box;
  margin-top: 2px;
  overflow: hidden;
  font-size: 13px;
  line-height: 1.5;
  color: var(--el-text-color-regular);
  white-space: pre-line;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.notif__row-meta {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.notif__empty {
  padding: 40px 16px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.notif__skeleton {
  padding: 12px 16px;
}
</style>
