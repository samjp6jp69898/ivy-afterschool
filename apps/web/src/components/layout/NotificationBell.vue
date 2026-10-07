<script setup lang="ts">
// FRONTEND-035：頂欄通知鈴。移植 ivy FE:src/components/layout/AdminNotificationBell.vue 的 badge + popover 結構；
// 資料改讀 notifications store（FRONTEND-034），面板為 NotificationPanel（FRONTEND-036）。
// 設計稿：docs/mockups/page-admin-shell.html（鈴 44×44、popover 寬 360 靠右無箭頭、第一次開啟才載入）。
// popover 不綁 v-model:visible：el-popover 不把 update:visible 轉給內部 tooltip，綁了就變成受控、點鈴不會開關；
// 改由 popover 自己處理點擊 / 鍵盤 / 點外面關閉，開啟以 before-enter 偵測，點通知後以 hide() 關閉。
// 面板內有可操作的按鈕，role 用 dialog（觸發鈕帶 aria-haspopup / aria-expanded），不用預設的 tooltip。
import { Bell } from '@element-plus/icons-vue'
import { ElMessage, type PopoverInstance } from 'element-plus'
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import NotificationPanel from '@/components/layout/NotificationPanel.vue'
import type { Notification } from '@/shared/types/api'
import { errorMessage } from '@/shared/utils/errorMessage'
import { useNotificationsStore } from '@/stores/notifications'

const store = useNotificationsStore()
const router = useRouter()
const popoverRef = ref<PopoverInstance | null>(null)

function load(unreadOnly = false): void {
  store.fetch({ unreadOnly }).catch((err: unknown) => {
    ElMessage.error(errorMessage(err, '無法載入通知，請稍後再試'))
  })
}

function onBeforeOpen(): void {
  if (!store.loaded) load()
}

/** 標已讀失敗只提示，不阻擋導頁 */
function onOpen(n: Notification): void {
  store.markRead(n.id).catch((err: unknown) => {
    ElMessage.error(errorMessage(err, '無法標為已讀，請稍後再試'))
  })
  popoverRef.value?.hide()
  if (n.deep_link) void router.push(n.deep_link)
}

async function onReadAll(): Promise<void> {
  try {
    await store.markAllRead()
  } catch (err) {
    ElMessage.error(errorMessage(err, '無法全部標為已讀，請稍後再試'))
  }
}
</script>

<template>
  <el-popover
    ref="popoverRef"
    role="dialog"
    trigger="click"
    placement="bottom-end"
    :width="360"
    :show-arrow="false"
    @before-enter="onBeforeOpen"
  >
    <template #reference>
      <el-button
        class="notification-bell"
        text
        aria-label="通知"
      >
        <el-badge
          :value="store.unreadCount"
          :max="99"
          :hidden="store.unreadCount === 0"
        >
          <el-icon :size="18">
            <Bell />
          </el-icon>
        </el-badge>
      </el-button>
    </template>
    <NotificationPanel
      :items="store.items"
      :unread-count="store.unreadCount"
      :loading="store.loading"
      @open="onOpen"
      @read-all="onReadAll"
      @filter="load"
    />
  </el-popover>
</template>

<style scoped>
.notification-bell {
  width: 44px;
  height: 44px;
  padding: 0;
  font-size: 18px;
}

.notification-bell :deep(.el-badge__content.is-fixed) {
  top: 2px;
  right: 6px;
}
</style>
