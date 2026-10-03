<script lang="ts">
export interface FilterOption {
  label: string
  value: string | number | boolean
}

/** 以 el-select 呈現；clearable 預設 true，清除 = 「全部」 */
export interface FilterGroup {
  key: string
  label: string
  options: FilterOption[]
  clearable?: boolean
}
</script>

<script setup lang="ts">
// FRONTEND-041：列表工具列（搜尋、下拉篩選、非下拉篩選 slot、筆數、動作區）。
// 移植 ivy FE:src/components/common/AdminListToolbar.vue 的 FilterGroup 與「全部」選項；去掉內建匯出與 mobile 切換。
// 設計稿：docs/mockups/component-common.html。
// - 搜尋 debounce 300ms 送出去頭尾空白的值；Enter 與清除（x）立即送出。
// - clearable 篩選的第一個選項固定是「全部」（平板沒有 hover，清除 icon 不一定點得到）；選「全部」與清除都 emit undefined。
// - total 為 undefined（例如載入中）不顯示筆數。
import { Search } from '@element-plus/icons-vue'
import { onBeforeUnmount, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    search?: string
    searchPlaceholder?: string
    filters?: FilterGroup[]
    filterValues?: Record<string, unknown>
    total?: number
  }>(),
  { search: '', searchPlaceholder: '搜尋', filters: () => [], filterValues: () => ({}), total: undefined },
)

const emit = defineEmits<{
  'update:search': [value: string]
  'update:filterValues': [value: Record<string, unknown>]
}>()

defineSlots<{
  'extra-filters'?: () => unknown
  actions?: () => unknown
}>()

const SEARCH_DEBOUNCE_MS = 300
const ALL = '__all__'

const text = ref(props.search)
let timer: ReturnType<typeof setTimeout> | null = null

watch(
  () => props.search,
  (v) => {
    if (v !== text.value.trim()) text.value = v
  },
)

function cancelTimer(): void {
  if (timer !== null) {
    clearTimeout(timer)
    timer = null
  }
}

function flushSearch(): void {
  cancelTimer()
  emit('update:search', text.value.trim())
}

function onSearchInput(value: string): void {
  text.value = value
  cancelTimer()
  timer = setTimeout(flushSearch, SEARCH_DEBOUNCE_MS)
}

onBeforeUnmount(cancelTimer)

function isClearable(group: FilterGroup): boolean {
  return group.clearable !== false
}

function selectValue(group: FilterGroup): FilterOption['value'] | undefined {
  const value = props.filterValues[group.key] as FilterOption['value'] | undefined
  return value === undefined && isClearable(group) ? ALL : value
}

function onSelect(group: FilterGroup, value: unknown): void {
  const next = value === ALL || value === '' || value === undefined ? undefined : value
  emit('update:filterValues', { ...props.filterValues, [group.key]: next })
}
</script>

<template>
  <div class="list-toolbar">
    <el-input
      class="list-toolbar__search"
      :model-value="text"
      :placeholder="searchPlaceholder"
      :aria-label="searchPlaceholder"
      :prefix-icon="Search"
      clearable
      @update:model-value="onSearchInput"
      @keyup.enter="flushSearch"
      @clear="flushSearch"
    />
    <div
      v-for="group in filters"
      :key="group.key"
      class="list-toolbar__filter"
    >
      <span class="list-toolbar__filter-label">{{ group.label }}</span>
      <el-select
        class="list-toolbar__select"
        :model-value="selectValue(group)"
        :clearable="isClearable(group) && selectValue(group) !== ALL"
        :aria-label="group.label"
        @update:model-value="(v: unknown) => onSelect(group, v)"
      >
        <el-option
          v-if="isClearable(group)"
          label="全部"
          :value="ALL"
        />
        <el-option
          v-for="option in group.options"
          :key="String(option.value)"
          :label="option.label"
          :value="option.value"
        />
      </el-select>
    </div>
    <div
      v-if="$slots['extra-filters']"
      class="list-toolbar__extra"
      data-test="toolbar-extra-filters"
    >
      <slot name="extra-filters" />
    </div>
    <span class="list-toolbar__spacer" />
    <span
      v-if="total !== undefined"
      class="list-toolbar__count"
      data-test="toolbar-count"
    >共 {{ total }} 筆</span>
    <div
      v-if="$slots.actions"
      class="list-toolbar__actions"
      data-test="toolbar-actions"
    >
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.list-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
}

.list-toolbar__search {
  width: 240px;
}

.list-toolbar__filter {
  display: flex;
  gap: 6px;
  align-items: center;
}

.list-toolbar__filter-label,
.list-toolbar__count {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.list-toolbar__select {
  width: 130px;
}

.list-toolbar__extra {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}

.list-toolbar__spacer {
  flex: 1 1 auto;
}

.list-toolbar__actions {
  display: flex;
  gap: 8px;
}

.list-toolbar__actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

/* 窄寬度：搜尋整列、篩選每列兩個等寬、筆數與 actions 同一列 */
@media (max-width: 767.98px) {
  .list-toolbar__search {
    width: 100%;
  }

  .list-toolbar__filter {
    flex: 1 1 calc(50% - 6px);
  }

  .list-toolbar__select {
    flex: 1;
    width: auto;
  }
}
</style>
