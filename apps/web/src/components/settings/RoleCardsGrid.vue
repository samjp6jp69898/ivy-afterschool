<script setup lang="ts">
// FRONTEND-067：角色卡清單（角色權限頁左欄、員工表單的角色單選共用）。
// 移植 ivy FE:src/components/settings/RoleCardsGrid.vue 的卡片版型與 v-model；去掉 super admin / parent flag。
// 設計稿：docs/mockups/page-settings-roles.html（RoleCardsGrid 段）。
// radiogroup：只有選中那張可 Tab 進入；方向鍵移到上一張 / 下一張並直接選取（頭尾循環）；Enter / 空白鍵選取。
import { computed, nextTick, ref } from 'vue'
import type { Role } from '@/api/roles'

const props = withDefaults(
  defineProps<{
    roles: Role[]
    modelValue: string | null
    disabled?: boolean
    showStaffCount?: boolean
  }>(),
  { disabled: false, showStaffCount: false },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const SYSTEM_ORDER = ['admin', 'director', 'clerk', 'tutor']

/** 系統角色依 admin → director → clerk → tutor，其餘依名稱 */
const sorted = computed(() =>
  [...props.roles].sort((a, b) => {
    const ia = SYSTEM_ORDER.indexOf(a.code)
    const ib = SYSTEM_ORDER.indexOf(b.code)
    if (ia !== -1 || ib !== -1) return (ia === -1 ? SYSTEM_ORDER.length : ia) - (ib === -1 ? SYSTEM_ORDER.length : ib)
    return a.name.localeCompare(b.name, 'zh-Hant')
  }),
)

const root = ref<HTMLElement | null>(null)

function pick(role: Role | undefined): void {
  if (!role || props.disabled || role.id === props.modelValue) return
  emit('update:modelValue', role.id)
}

const STEP: Record<string, number> = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }

function onKeydown(e: KeyboardEvent, index: number): void {
  if (props.disabled) return
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    pick(sorted.value[index])
    return
  }
  const delta = STEP[e.key]
  if (!delta) return
  e.preventDefault()
  const count = sorted.value.length
  const next = (index + delta + count) % count
  pick(sorted.value[next])
  void nextTick(() => root.value?.querySelectorAll<HTMLElement>('.role-card')[next]?.focus())
}

function tabIndex(role: Role, index: number): number {
  if (props.disabled) return -1
  const hasSelected = sorted.value.some((r) => r.id === props.modelValue)
  return role.id === props.modelValue || (!hasSelected && index === 0) ? 0 : -1
}
</script>

<template>
  <div
    ref="root"
    class="role-grid"
    role="radiogroup"
    aria-label="角色"
  >
    <div
      v-for="(r, i) in sorted"
      :key="r.id"
      class="role-card"
      role="radio"
      :aria-checked="r.id === modelValue ? 'true' : 'false'"
      :aria-disabled="disabled ? 'true' : undefined"
      :tabindex="tabIndex(r, i)"
      :class="{ 'is-selected': r.id === modelValue, 'is-disabled': disabled }"
      @click="pick(r)"
      @keydown="onKeydown($event, i)"
    >
      <div class="role-card__top">
        <span class="role-card__name">{{ r.name }}</span>
        <el-tag
          v-if="r.is_system"
          size="small"
          type="info"
          disable-transitions
        >
          系統
        </el-tag>
      </div>
      <span class="role-card__code">{{ r.code }}</span>
      <div class="role-card__meta">
        <span>{{ r.effective_permissions.length }} 項權限</span>
        <template v-if="showStaffCount">
          <span class="role-card__dot">・</span>
          <span>{{ r.staff_count }} 位員工</span>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 自動欄數：左欄 280px 時一欄、員工表單裡多欄 */
.role-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 8px;
}

.role-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-height: 76px;
  padding: 10px 12px;
  text-align: left;
  cursor: pointer;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  transition:
    border-color 0.15s,
    background-color 0.15s;
}

.role-card:hover {
  border-color: var(--el-color-primary-light-5);
}

.role-card:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}

.role-card.is-selected {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary);
  box-shadow: inset 0 0 0 1px var(--el-color-primary);
}

.role-card.is-disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.role-card.is-disabled:hover {
  border-color: var(--el-border-color-light);
}

.role-card__top {
  display: flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
}

.role-card__name {
  overflow: hidden;
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.role-card__code {
  font-family: var(--el-font-family-mono, ui-monospace, monospace);
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.role-card__meta {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 12px;
  color: var(--el-text-color-regular);
}

.role-card__dot {
  color: var(--el-text-color-placeholder);
}

@media (prefers-reduced-motion: reduce) {
  .role-card {
    transition: none;
  }
}
</style>
