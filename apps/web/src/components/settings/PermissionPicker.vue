<script setup lang="ts">
// FRONTEND-068：權限勾選器（角色詳情、新增角色、員工個別權限共用）。
// 移植 ivy FE:src/components/settings/PermissionPicker.vue 的分組摺疊、搜尋、整組全選；
// 去掉 navigation manifest 頁面樹、scope 選項、shared view 提示。
// 設計稿：docs/mockups/page-settings-roles.html（PermissionPicker 段）。
// 新增受 grantable 限制（後端只限制新增）；已勾選但不可授出的碼仍可取消。整組全選只勾可授出的碼。
import { ArrowDown, ArrowRight, Search } from '@element-plus/icons-vue'
import { computed, reactive, ref } from 'vue'
import type { PermissionCatalog } from '@/api/roles'
import EmptyState from '@/components/common/EmptyState.vue'

type Group = PermissionCatalog['groups'][number]

const props = withDefaults(
  defineProps<{
    catalog: PermissionCatalog
    modelValue: string[]
    /** 目前使用者持有的權限；未提供 = 全部可授 */
    grantable?: ReadonlySet<string>
    /** 例如角色基準，顯示「角色預設」小標 */
    highlight?: ReadonlySet<string>
    disabled?: boolean
  }>(),
  { grantable: undefined, highlight: undefined, disabled: false },
)

const emit = defineEmits<{ 'update:modelValue': [value: string[]] }>()

const keyword = ref('')
const collapsed = reactive<Record<string, boolean>>({})

const selected = computed(() => new Set(props.modelValue))
const total = computed(() => props.catalog.groups.reduce((n, g) => n + g.permissions.length, 0))
const query = computed(() => keyword.value.trim().toLowerCase())

const visibleGroups = computed(() =>
  props.catalog.groups
    .map((g) => ({
      ...g,
      items: g.permissions.filter(
        (p) => !query.value || p.label.toLowerCase().includes(query.value) || p.code.toLowerCase().includes(query.value),
      ),
    }))
    .filter((g) => g.items.length > 0),
)

function canGrant(code: string): boolean {
  return !props.grantable || props.grantable.has(code)
}

function itemDisabled(code: string): boolean {
  return props.disabled || (!selected.value.has(code) && !canGrant(code))
}

/** 搜尋時強制展開 */
function isOpen(g: Group): boolean {
  return !!query.value || !collapsed[g.key]
}

function groupCount(g: Group): number {
  return g.permissions.filter((p) => selected.value.has(p.code)).length
}

function groupChecked(g: Group): boolean {
  return groupCount(g) === g.permissions.length
}

function groupIndeterminate(g: Group): boolean {
  const n = groupCount(g)
  return n > 0 && n < g.permissions.length
}

function groupDisabled(g: Group): boolean {
  return props.disabled || g.permissions.every((p) => itemDisabled(p.code))
}

function emitSet(set: Set<string>): void {
  emit('update:modelValue', [...set].sort())
}

function toggle(code: string, on: boolean): void {
  const next = new Set(selected.value)
  if (on) next.add(code)
  else next.delete(code)
  emitSet(next)
}

/** 整組勾選只加可授出的碼；取消整組則全部取消 */
function toggleGroup(g: Group, on: boolean): void {
  const next = new Set(selected.value)
  for (const p of g.permissions) {
    if (!on) next.delete(p.code)
    else if (canGrant(p.code)) next.add(p.code)
  }
  emitSet(next)
}

function toggleCollapse(g: Group): void {
  collapsed[g.key] = !collapsed[g.key]
}
</script>

<template>
  <div class="perm-picker">
    <div class="perm-picker__toolbar">
      <el-input
        v-model="keyword"
        class="perm-picker__search"
        placeholder="搜尋權限名稱或代碼"
        aria-label="搜尋權限名稱或代碼"
        clearable
        :prefix-icon="Search"
      />
      <span class="perm-picker__count">已選 {{ modelValue.length }} / {{ total }}</span>
    </div>

    <EmptyState
      v-if="!visibleGroups.length"
      variant="inline"
      title="找不到符合的權限"
    />

    <div
      v-for="g in visibleGroups"
      :key="g.key"
      class="perm-group"
      :class="{ 'is-open': isOpen(g) }"
    >
      <div class="perm-group__head">
        <el-button
          class="perm-group__toggle"
          text
          :aria-expanded="String(isOpen(g))"
          :aria-label="`展開或收合 ${g.label}`"
          :disabled="!!query"
          :icon="isOpen(g) ? ArrowDown : ArrowRight"
          @click="toggleCollapse(g)"
        />
        <el-checkbox
          :model-value="groupChecked(g)"
          :indeterminate="groupIndeterminate(g)"
          :disabled="groupDisabled(g)"
          @change="(v) => toggleGroup(g, v === true)"
        >
          {{ g.label }}
        </el-checkbox>
        <span class="perm-group__count">已選 {{ groupCount(g) }} / {{ g.permissions.length }}</span>
      </div>
      <div
        v-if="isOpen(g)"
        class="perm-group__body"
      >
        <div
          v-for="p in g.items"
          :key="p.code"
          class="perm-item"
        >
          <el-tooltip
            :disabled="disabled || canGrant(p.code)"
            content="你沒有此權限，無法授出"
            placement="top"
          >
            <el-checkbox
              :model-value="selected.has(p.code)"
              :disabled="itemDisabled(p.code)"
              @change="(v) => toggle(p.code, v === true)"
            >
              <span class="perm-item__text">
                <span class="perm-item__label">
                  {{ p.label }}
                  <el-tag
                    v-if="highlight?.has(p.code)"
                    class="perm-item__base"
                    size="small"
                    type="info"
                    effect="plain"
                    disable-transitions
                  >
                    角色預設
                  </el-tag>
                </span>
                <span class="perm-item__code">{{ p.code }}</span>
              </span>
            </el-checkbox>
          </el-tooltip>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.perm-picker {
  width: 100%;
}

.perm-picker__toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 10px;
}

.perm-picker__search {
  width: 240px;
}

.perm-picker__count {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.perm-group {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}

.perm-group + .perm-group {
  margin-top: 8px;
}

.perm-group__head {
  display: flex;
  gap: 4px;
  align-items: center;
  padding: 4px 12px 4px 4px;
  background: var(--el-fill-color-lighter);
  border-radius: 6px;
}

.perm-group.is-open .perm-group__head {
  border-bottom: 1px solid var(--el-border-color-lighter);
  border-radius: 6px 6px 0 0;
}

.perm-group__toggle {
  width: 32px;
  height: 32px;
  padding: 0;
}

.perm-group__head .el-checkbox {
  font-weight: 600;
}

.perm-group__count {
  margin-left: auto;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-secondary);
}

.perm-group__body {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 2px 16px;
  padding: 8px 12px 8px 40px;
}

.perm-item {
  display: flex;
  align-items: flex-start;
  min-height: 40px;
}

.perm-item :deep(.el-checkbox) {
  align-items: flex-start;
  height: auto;
  white-space: normal;
}

.perm-item :deep(.el-checkbox__input) {
  margin-top: 3px;
}

.perm-item__text {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
}

.perm-item__label {
  font-size: 14px;
  color: var(--el-text-color-regular);
}

.perm-item__code {
  font-family: var(--el-font-family-mono, ui-monospace, monospace);
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.perm-item__base {
  margin-left: 4px;
}

.perm-item :deep(.el-checkbox.is-disabled) .perm-item__label {
  color: var(--el-text-color-placeholder);
}
</style>
