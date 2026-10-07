<script setup lang="ts">
/**
 * 單次考試各科成績（純展示）：語意化 table，欄位「科目」「分數」「滿分」，caption「各科成績」視覺隱藏。
 * 分數：缺考優先 →「缺考」（error 色）；沒有分數且非缺考 →「未登記」；其餘為數字（整數不帶小數，
 * 非整數至多 1 位）。科目備註放在同一列、科目名稱下方，螢幕閱讀器念到該科就會念到備註。
 * 不依分數高低上色、不顯示總分 / 平均 / 排名（domain_spec M8：不做排名，平均只在後台）。
 * subjects 為空時不渲染 table，改用共用空狀態。
 */
import { computed } from 'vue'
import type { ParentExamDetail } from '../../api/exams'
import ParentEmptyState from '../ParentEmptyState.vue'

const props = defineProps<{ subjects: ParentExamDetail['subjects'] }>()

type Subject = ParentExamDetail['subjects'][number]

/** 整數不帶小數（88 而非 88.0），非整數至多 1 位（92.5） */
function formatScore(n: number): string {
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10)
}

function scoreCell(s: Subject): { text: string; state: string } {
  if (s.is_absent) return { text: '缺考', state: 'is-absent' }
  // null / undefined / 非有限數字都視為尚未登記
  if (s.score === null || !Number.isFinite(s.score)) return { text: '未登記', state: 'is-unrecorded' }
  return { text: formatScore(s.score), state: '' }
}

const rows = computed(() =>
  props.subjects.map((s) => ({
    name: s.subject_name,
    note: s.note,
    score: scoreCell(s),
    full: formatScore(s.full_score),
  })),
)
</script>

<template>
  <div class="score">
    <ParentEmptyState
      v-if="!subjects.length"
      icon="grading"
      title="這次考試尚無科目成績"
    />
    <table
      v-else
      class="score-table"
    >
      <caption class="visually-hidden">
        各科成績
      </caption>
      <thead>
        <tr>
          <th
            scope="col"
            class="m3-label-medium"
          >
            科目
          </th>
          <th
            scope="col"
            class="col-score m3-label-medium"
          >
            分數
          </th>
          <th
            scope="col"
            class="col-full m3-label-medium"
          >
            滿分
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(r, i) in rows"
          :key="i"
        >
          <td>
            <span class="score-table__subject m3-body-large">{{ r.name }}</span>
            <span
              v-if="r.note"
              class="score-table__note m3-body-small"
            >{{ r.note }}</span>
          </td>
          <td class="col-score">
            <span
              class="score-table__score m3-title-medium"
              :class="r.score.state"
            >{{ r.score.text }}</span>
          </td>
          <td class="col-full">
            <span class="score-table__full m3-body-medium">{{ r.full }}</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.score-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}

.score-table th {
  height: 40px;
  padding: 0 16px;
  border-bottom: 1px solid var(--m3-outline-variant);
  color: var(--m3-on-surface-variant);
  text-align: left;
}

.score-table td {
  padding: 12px 16px;
  vertical-align: top;
}

.score-table tbody tr + tr td {
  border-top: 1px solid var(--m3-outline-variant);
}

/* 分數欄與滿分欄固定寬、靠右對齊，其餘寬度給科目 */
.score-table .col-score {
  width: 84px;
  text-align: right;
}

.score-table .col-full {
  width: 72px;
  text-align: right;
}

.score-table__subject {
  display: block;
  color: var(--m3-on-surface);
  overflow-wrap: anywhere;
}

.score-table__note {
  display: block;
  margin-top: 2px;
  color: var(--m3-on-surface-variant);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.score-table__score {
  color: var(--m3-on-surface);
  font-variant-numeric: tabular-nums;
}

.score-table__score.is-absent {
  color: var(--m3-error);
}

.score-table__score.is-unrecorded {
  color: var(--m3-on-surface-variant);
  font-weight: 400;
}

.score-table__full {
  color: var(--m3-on-surface-variant);
  font-variant-numeric: tabular-nums;
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
