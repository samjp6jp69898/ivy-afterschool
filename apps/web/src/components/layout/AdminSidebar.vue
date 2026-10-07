<script setup lang="ts">
// FRONTEND-031：後台側欄。移植 ivy FE:src/components/layout/AdminSidebar.vue 的 el-menu 結構、收合按鈕與
// 「選單由 manifest 衍生」；去掉 tenant branding、platform admin 分支、PERMISSION_NAMES 全量預算。
// 設計稿：docs/mockups/page-admin-shell.html。收合狀態由 AdminLayout（FRONTEND-029）持有並記住，
// 這裡只顯示與 emit toggle；drawer 為手機抽屜模式（填滿抽屜、沒有收合按鈕）。
// 選單只決定 UI 顯示，安全邊界是路由守衛與後端 require_permission。
import {
  ArrowLeft,
  ArrowRight,
  Calendar,
  Collection,
  DataLine,
  EditPen,
  List,
  Lock,
  Odometer,
  School,
  Setting,
  Stamp,
  Tickets,
  Trophy,
  User,
  UserFilled,
  Van,
} from '@element-plus/icons-vue'
import { computed, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { visibleNavigation } from '@/constants/navigation'
import { useAuthStore } from '@/stores/auth'

const props = withDefaults(defineProps<{ collapsed: boolean; drawer?: boolean }>(), { drawer: false })

const emit = defineEmits<{
  navigate: [path: string]
  toggle: []
}>()

/** navigation.ts 的 icon 名稱 → 元件；只列選單用到的，避免整包 icons-vue 進 bundle */
const NAV_ICONS: Record<string, Component> = {
  Odometer,
  Calendar,
  EditPen,
  Van,
  Stamp,
  Tickets,
  User,
  School,
  DataLine,
  Trophy,
  Setting,
  Collection,
  UserFilled,
  Lock,
  List,
}

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const groups = computed(() => visibleNavigation(auth.permissions))
const isCollapsed = computed(() => props.collapsed && !props.drawer)

function segments(path: string): string[] {
  return path.split('/').filter(Boolean)
}

/** 以 `/` 分段做最長前綴比對：/exams/e1 → /exams；`/` 只在首頁本身高亮 */
const activePath = computed(() => {
  const current = segments(route.path)
  let best: string | null = null
  let bestLength = -1
  for (const group of groups.value) {
    for (const item of group.items) {
      const target = segments(item.path)
      const matched =
        item.path === '/' ? route.path === '/' : target.every((segment, i) => current[i] === segment)
      if (matched && target.length > bestLength) {
        best = item.path
        bestLength = target.length
      }
    }
  }
  return best ?? ''
})

function onSelect(path: string): void {
  emit('navigate', path)
  void router.push(path)
}
</script>

<template>
  <aside
    class="admin-sidebar"
    :class="{ 'is-collapsed': isCollapsed, 'is-drawer': drawer }"
    data-test="admin-sidebar"
  >
    <div
      class="sidebar__brand"
      data-test="sidebar-brand"
    >
      <el-icon class="sidebar__brand-icon">
        <School />
      </el-icon>
      <span
        v-show="!isCollapsed"
        data-test="sidebar-brand-text"
      >安親班管理系統</span>
    </div>

    <div class="sidebar__menu">
      <div
        v-if="groups.length === 0"
        v-show="!isCollapsed"
        class="sidebar__empty"
      >
        沒有可使用的功能，請聯絡管理員
      </div>
      <el-menu
        v-else
        :default-active="activePath"
        :collapse="isCollapsed"
        :collapse-transition="false"
        @select="onSelect"
      >
        <el-menu-item-group
          v-for="group in groups"
          :key="group.key"
          :title="group.label"
        >
          <el-menu-item
            v-for="item in group.items"
            :key="item.path"
            :index="item.path"
          >
            <el-icon>
              <component :is="NAV_ICONS[item.icon]" />
            </el-icon>
            <template #title>
              {{ item.label }}
            </template>
          </el-menu-item>
        </el-menu-item-group>
      </el-menu>
    </div>

    <div
      v-if="!drawer"
      class="sidebar__collapse"
    >
      <el-button
        text
        :aria-label="collapsed ? '展開側欄' : '收合側欄'"
        @click="emit('toggle')"
      >
        <el-icon>
          <ArrowRight v-if="collapsed" />
          <ArrowLeft v-else />
        </el-icon>
        <span v-if="!collapsed">收合</span>
      </el-button>
    </div>
  </aside>
</template>

<style scoped>
.admin-sidebar {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  width: 220px;
  height: 100%;
  background: var(--el-bg-color);
  border-right: 1px solid var(--el-border-color-light);
  transition: width 0.2s ease;
}

.admin-sidebar.is-collapsed {
  width: 64px;
}

.admin-sidebar.is-drawer {
  width: 100%;
  border-right: none;
}

.sidebar__brand {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
  align-items: center;
  height: 56px;
  padding: 0 22px;
  overflow: hidden;
  font-weight: 600;
  color: var(--el-text-color-primary);
  white-space: nowrap;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.sidebar__brand-icon {
  flex-shrink: 0;
  font-size: 20px;
  color: var(--el-color-primary);
}

.sidebar__menu {
  flex: 1;
  min-height: 0;
  overflow: hidden auto;
}

.sidebar__menu :deep(.el-menu) {
  border-right: none;
}

.sidebar__menu :deep(.el-menu-item-group__title) {
  padding-top: 14px;
  font-size: 12px;
}

/* 收合時群組標題改成細分隔線 */
.sidebar__menu :deep(.el-menu--collapse .el-menu-item-group__title) {
  height: 1px;
  padding: 0;
  margin: 8px 12px;
  overflow: hidden;
  background: var(--el-border-color-lighter);
}

.sidebar__menu :deep(.el-menu-item) {
  height: 44px;
  line-height: 44px;
}

.sidebar__empty {
  padding: 24px 16px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.sidebar__collapse {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  height: 44px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.sidebar__collapse .el-icon + span {
  margin-left: 4px;
}

@media (prefers-reduced-motion: reduce) {
  .admin-sidebar {
    transition: none;
  }
}
</style>
