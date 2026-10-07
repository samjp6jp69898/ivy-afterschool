<script setup lang="ts">
/**
 * 考試列（純展示）：根元素 li，整列是一個 RouterLink 到 /exams/{exam_id}（可及名稱即列內文字）。
 * 第一行考試名稱（最多兩行），第二行類型標籤 + 日期（含星期）+ 科目數，右側 chevron。
 * 列表只做索引：不顯示分數、平均、名次，也不顯示公布時間。
 * 空清單、載入中、載入更多與錯誤屬於 ExamsView。
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { formatDate, weekdayLabel } from '@/shared/utils/datetime'
import type { ParentExamSummary } from '../../api/exams'
import M3Icon from '../m3/M3Icon.vue'

const props = defineProps<{ exam: ParentExamSummary }>()

// exam_id 當成單一路徑段編碼，含 / ? 的值不會改變路徑
const to = computed(() => `/exams/${encodeURIComponent(props.exam.exam_id)}`)

// 'YYYY/MM/DD（X）'；日期不合法時只顯示 formatDate 的佔位符，避免 weekdayLabel 丟例外
const dateText = computed(() => {
  const date = formatDate(props.exam.exam_date)
  return date === '—' ? date : `${date}（${weekdayLabel(props.exam.exam_date)}）`
})
</script>

<template>
  <li class="exam-item">
    <RouterLink
      class="exam-item__link"
      :to="to"
    >
      <span class="exam-item__body">
        <span class="exam-item__name m3-title-medium">{{ exam.name }}</span>
        <span class="exam-item__meta m3-body-medium">
          <span class="exam-item__type m3-label-medium">{{ exam.exam_type_name }}</span>
          <span>{{ dateText }}</span><span
            class="exam-item__dot"
            aria-hidden="true"
          >·</span><span>{{ exam.subject_count }} 科</span>
        </span>
      </span>
      <M3Icon
        class="exam-item__chevron"
        name="chevron_right"
      />
    </RouterLink>
  </li>
</template>

<style scoped>
.exam-item {
  list-style: none;
}

/* 列間分隔線由列自己畫 */
.exam-item + .exam-item {
  border-top: 1px solid var(--m3-outline-variant);
}

.exam-item__link {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 72px;
  padding: 12px 8px 12px 16px;
  color: var(--m3-on-surface);
  text-decoration: none;
  -webkit-tap-highlight-color: transparent;
}

/* state layer：hover 0.08 / focus 0.12 / pressed 0.12 */
.exam-item__link::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--m3-on-surface);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--m3-dur-short-2) var(--m3-easing-standard);
}

.exam-item__link:hover::before {
  opacity: var(--m3-state-hover);
}

.exam-item__link:focus-visible::before {
  opacity: var(--m3-state-focus);
}

.exam-item__link:active::before {
  opacity: var(--m3-state-pressed);
}

.exam-item__link:focus-visible {
  outline: 2px solid var(--m3-primary);
  outline-offset: -2px;
}

.exam-item__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

/* 名稱最多兩行，第二行尾端 …；英數長字串不撐破版面 */
.exam-item__name {
  display: -webkit-box;
  overflow: hidden;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.exam-item__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  color: var(--m3-on-surface-variant);
  font-variant-numeric: tabular-nums;
}

/* 類型只是分類不承載狀態：用 tertiary 與作業科目標籤（surface-container-high）區分 */
.exam-item__type {
  display: inline-block;
  max-width: 96px;
  height: 24px;
  padding: 0 8px;
  border-radius: var(--m3-shape-extra-small);
  background: var(--m3-tertiary-container);
  color: var(--m3-on-tertiary-container);
  line-height: 24px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.exam-item__dot {
  color: var(--m3-outline);
}

.exam-item__chevron {
  flex: none;
  color: var(--m3-on-surface-variant);
}
</style>
