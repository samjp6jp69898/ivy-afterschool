<script setup lang="ts">
// FRONTEND-123：出勤狀態摘要 chip（StatCard 當篩選 chip）+ 出席率。設計稿：docs/mockups/page-attendance-today.html。
// 點已選中的 chip 或「全部」都回到全部（emit null）；active 為 null 時「全部」呈選中。
import { computed } from 'vue'
import { ATTENDANCE_STATUS_META, type Tone } from '@/shared/constants/statusLabels'
import type { AttendanceStatus } from '@/shared/types/api'
import StatCard from '@/components/common/StatCard.vue'

export interface AttendanceSummary {
  total: number
  expected: number
  present: number
  left: number
  absent: number
  leave: number
}

const props = withDefaults(
  defineProps<{
    summary: AttendanceSummary
    active: AttendanceStatus | null
    loading?: boolean
  }>(),
  { loading: false },
)

const emit = defineEmits<{ 'update:active': [status: AttendanceStatus | null] }>()

const STATUS_ORDER: AttendanceStatus[] = ['expected', 'present', 'left', 'absent', 'leave']

interface Chip {
  key: string
  label: string
  value: number
  tone: Tone
  status: AttendanceStatus | null
}

const chips = computed<Chip[]>(() => [
  { key: 'all', label: '全部', value: props.summary.total, tone: 'info', status: null },
  ...STATUS_ORDER.map((status) => ({
    key: status,
    label: ATTENDANCE_STATUS_META[status].label,
    value: props.summary[status],
    tone: ATTENDANCE_STATUS_META[status].tone,
    status,
  })),
])

/** (已到 + 已離) ÷ (全部 − 請假)，取整數百分比；分母 0 顯示「—」 */
const attendanceRate = computed(() => {
  const { total, leave, present, left } = props.summary
  const denominator = total - leave
  return denominator <= 0 ? '—' : `${Math.round(((present + left) / denominator) * 100)}%`
})

function toggle(status: AttendanceStatus | null): void {
  emit('update:active', status === null || props.active === status ? null : status)
}
</script>

<template>
  <div
    class="summary-bar"
    role="group"
    aria-label="出勤狀態篩選"
  >
    <StatCard
      v-for="c in chips"
      :key="c.key"
      :label="c.label"
      :value="c.value"
      :tone="c.tone"
      :loading="loading"
      clickable
      :active="active === c.status"
      @click="toggle(c.status)"
    />
    <StatCard
      data-test="attendance-rate"
      label="出席率"
      :loading="loading"
      :value="attendanceRate"
      hint="（已到 + 已離）÷（全部 − 請假）"
    />
  </div>
</template>

<style scoped>
/* 6 張 chip 等寬，最右的出席率稍寬 */
.summary-bar {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr)) minmax(0, 1.1fr);
  gap: 8px;
}
</style>
