<script setup lang="ts">
// FRONTEND-049：403 無權限與 404 找不到頁面。參考 ivy FE:src/views/ErrorStateView.vue 的版面；
// 去掉 maintenance / chunk error 類型、Sentry 與「重新登入」按鈕（登出走頂欄選單）。
// 設計稿：docs/mockups/page-error.html。403 在 AdminLayout 內容區置中；404 路由為 blank layout，
// App 直接渲染本元件，由本元件撐滿整頁並置中。
import { Compass, Lock } from '@element-plus/icons-vue'
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { firstAllowedPath } from '@/router/authGuard'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{ kind: 'forbidden' | 'not_found' }>()

const COPY = {
  forbidden: {
    title: '沒有權限',
    description: '你的帳號沒有檢視此頁面的權限，如需使用請聯絡主任或管理員。',
    icon: Lock,
  },
  not_found: { title: '找不到頁面', description: '網址可能有誤或頁面已移除。', icon: Compass },
} as const

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const copy = computed(() => COPY[props.kind])
const isPage = computed(() => route.meta.layout === 'blank')

/** 只在 403 顯示；只顯示不做成連結（再點一次也是 403） */
const fromPath = computed(() => {
  if (props.kind !== 'forbidden') return ''
  const raw = route.query.from
  const value = Array.isArray(raw) ? raw[0] : raw
  return typeof value === 'string' ? value : ''
})

/** 已登入 → 第一個有權限的頁（沒有則 null，按鈕停用）；未登入 → /login */
const homePath = computed(() => (auth.isAuthenticated ? firstAllowedPath(auth.permissions) : '/login'))

function goHome(): void {
  // replace：按上一頁不會回到錯誤頁
  if (homePath.value) void router.replace(homePath.value)
}
</script>

<template>
  <div
    class="error-view"
    :class="{ 'is-page': isPage }"
    data-test="error-view"
  >
    <div
      class="error-view__icon"
      :class="kind === 'forbidden' ? 'is-forbidden' : 'is-not-found'"
      aria-hidden="true"
    >
      <el-icon>
        <component :is="copy.icon" />
      </el-icon>
    </div>
    <h1 class="error-view__title">
      {{ copy.title }}
    </h1>
    <p class="error-view__desc">
      {{ copy.description }}
    </p>
    <p
      v-if="fromPath"
      class="error-view__from"
    >
      嘗試前往：<code>{{ fromPath }}</code>
    </p>
    <div class="error-view__action">
      <el-button
        type="primary"
        :disabled="!homePath"
        @click="goHome"
      >
        回到首頁
      </el-button>
      <span
        v-if="!homePath"
        class="error-view__hint"
      >你的帳號目前沒有可使用的頁面</span>
    </div>
  </div>
</template>

<style scoped>
.error-view {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  padding: 48px 16px;
  text-align: center;
}

/* blank layout（404）：撐滿整個視窗、灰底 */
.error-view.is-page {
  min-height: 100vh;
  background: var(--el-bg-color-page);
}

.error-view__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 72px;
  height: 72px;
  margin-bottom: 20px;
  font-size: 34px;
  border-radius: 50%;
}

.error-view__icon.is-forbidden {
  color: var(--el-color-warning-dark-2);
  background: var(--el-color-warning-light-9);
}

.error-view__icon.is-not-found {
  color: var(--el-color-info);
  background: var(--el-fill-color-dark);
}

.error-view__title {
  margin: 0 0 8px;
  font-size: 20px;
  font-weight: 600;
  line-height: 28px;
  color: var(--el-text-color-primary);
}

.error-view__desc {
  max-width: 420px;
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
}

.error-view__from {
  max-width: 420px;
  margin: 12px 0 13px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  word-break: break-all;
}

.error-view__from code {
  padding: 1px 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--el-text-color-regular);
  background: var(--el-fill-color-light);
  border-radius: 4px;
}

.error-view__action {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  margin-top: 24px;
}

.error-view__action .el-button {
  min-width: 120px;
  height: 40px;
}

.error-view__hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
