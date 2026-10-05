<script setup lang="ts">
/**
 * 作業項目列（純展示）：左側科目標籤、中間標題、右側狀態膠囊。
 * 根元素 li，外層 ul 與 aria-label 由宿主負責；列間分隔線由列自己畫。
 */
import { computed } from 'vue'
import { HOMEWORK_ITEM_STATUS_META } from '@/shared/constants/statusLabels'
import type { HomeworkItemStatus } from '@/shared/types/api'
import StatusPill from '../StatusPill.vue'

const props = defineProps<{
  item: { title: string; subject_name: string | null; status: HomeworkItemStatus }
}>()

const meta = computed(() => HOMEWORK_ITEM_STATUS_META[props.item.status])
const isDone = computed(() => props.item.status === 'done')
</script>

<template>
  <li
    class="hw-row"
    :class="{ 'is-done': isDone }"
  >
    <span class="hw-row__subject m3-label-medium">{{ item.subject_name ?? '其他' }}</span>
    <p class="hw-row__title m3-body-large">
      {{ item.title }}
    </p>
    <StatusPill
      class="hw-row__status"
      :label="meta.label"
      :tone="meta.tone"
      :icon="isDone ? 'check' : ''"
    />
  </li>
</template>

<style scoped>
.hw-row {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 64px;
  padding: 12px 16px;
  list-style: none;
}

.hw-row + .hw-row {
  border-top: 1px solid var(--m3-outline-variant);
}

/* 固定寬讓整欄標題左緣對齊；超過 4 字單行省略 */
.hw-row__subject {
  display: inline-block;
  flex: none;
  width: 60px;
  height: 24px;
  padding: 0 4px;
  border-radius: var(--m3-shape-extra-small);
  background: var(--m3-surface-container-high);
  color: var(--m3-on-surface-variant);
  line-height: 24px;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hw-row__title {
  display: -webkit-box;
  flex: 1;
  min-width: 0;
  margin: 0;
  overflow: hidden;
  color: var(--m3-on-surface);
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.hw-row.is-done .hw-row__title {
  color: var(--m3-on-surface-variant);
}

.hw-row__status {
  flex: none;
}
</style>
