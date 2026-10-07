<script setup lang="ts">
/**
 * 月出勤日曆（純展示）：週日開頭的 7 欄格，表頭「日 一 二 三 四 五 六」，月初前補空格。
 * 每格是 <button>：日期數字 + 狀態以「底色 + 圖示」雙重表達，aria-label 如「10月2日 已到班」。
 * 請假格在月曆與圖例改用 tertiary-container，與已離班（secondary-container）區分；
 * 今天以前仍是 expected 的日子顯示為「未登記」；非營業日灰階、無紀錄無底色，兩者仍可點。
 * 日期與星期一律以 Date.UTC 計算，不受執行環境時區影響（不用 toISOString().slice）。
 * 月份切換、統計列、單日明細與載入 / 錯誤狀態屬於 AttendanceView，本元件沒有自己的空狀態。
 */
import { computed } from 'vue'
import { ATTENDANCE_STATUS_META, LEAVE_TYPE_META, statusMeta } from '@/shared/constants/statusLabels'
import type { AttendanceStatus, LeaveType } from '@/shared/types/api'
import type { AttendanceDay } from '../../api/attendance'
import M3Icon from '../m3/M3Icon.vue'

const props = defineProps<{
  /** YYYY-MM */
  month: string
  days: AttendanceDay[]
  /** YYYY-MM-DD，台北今天（由頁面傳入） */
  today: string
  selectedDate: string | null
}>()

const emit = defineEmits<{ select: [date: string] }>()

type CellKind = AttendanceStatus | 'unrecorded' | 'closed' | 'none'

interface DayCell {
  date: string
  day: number
  kind: CellKind
  label: string
  icon: string
  mark: string
}

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']
const UNRECORDED_LABEL = '未登記'
const CLOSED_LABEL = '休息'
const NO_RECORD_LABEL = '無紀錄'

const MONTH_RE = /^(\d{4})-(0[1-9]|1[0-2])$/

// 狀態圖示與底色一起表達狀態；沒有圖示的狀態（休息）改用文字標記
const CELL_ICONS: Partial<Record<CellKind, string>> = {
  present: 'check',
  left: 'done_all',
  leave: 'event_busy',
  absent: 'close',
  expected: 'schedule',
  unrecorded: 'remove',
}
const CELL_MARKS: Partial<Record<CellKind, string>> = { closed: '休' }

const LEGEND: { kind: CellKind; label: string }[] = [
  { kind: 'present', label: ATTENDANCE_STATUS_META.present.label },
  { kind: 'left', label: ATTENDANCE_STATUS_META.left.label },
  { kind: 'leave', label: ATTENDANCE_STATUS_META.leave.label },
  { kind: 'absent', label: ATTENDANCE_STATUS_META.absent.label },
  { kind: 'expected', label: ATTENDANCE_STATUS_META.expected.label },
  { kind: 'unrecorded', label: UNRECORDED_LABEL },
  { kind: 'closed', label: CLOSED_LABEL },
]

// 病假 / 事假直接念假別；其他念「請假（其他）」，單寫「其他」聽不出是請假
function leaveLabel(type: LeaveType | null): string {
  const base = ATTENDANCE_STATUS_META.leave.label
  if (!type) return base
  return type === 'other' ? `${base}（${LEAVE_TYPE_META.other.label}）` : statusMeta(LEAVE_TYPE_META, type).label
}

function describe(date: string, info: AttendanceDay | undefined, today: string): { kind: CellKind; text: string } {
  if (info?.is_service_day === false) return { kind: 'closed', text: CLOSED_LABEL }
  const status = info?.status ?? null
  if (!info || !status) return { kind: 'none', text: NO_RECORD_LABEL }
  // 今天以前仍是 expected：老師沒登記到班；今天與之後仍是預計到班
  if (status === 'expected' && date < today) return { kind: 'unrecorded', text: UNRECORDED_LABEL }
  if (status === 'leave') return { kind: 'leave', text: leaveLabel(info.leave_type) }
  return { kind: status, text: statusMeta(ATTENDANCE_STATUS_META, status).label }
}

const grid = computed<{ lead: number; days: DayCell[] }>(() => {
  const m = MONTH_RE.exec(props.month)
  if (!m) return { lead: 0, days: [] }
  const year = Number(m[1])
  const month = Number(m[2])
  // 月初是週幾 = 前導空格數；Date.UTC(y, m, 0) 是當月最後一天
  const lead = new Date(Date.UTC(year, month - 1, 1)).getUTCDay()
  const total = new Date(Date.UTC(year, month, 0)).getUTCDate()
  const byDate = new Map(props.days.map((d) => [d.date, d]))
  const days: DayCell[] = []
  for (let n = 1; n <= total; n++) {
    const date = `${props.month}-${String(n).padStart(2, '0')}`
    const { kind, text } = describe(date, byDate.get(date), props.today)
    days.push({
      date,
      day: n,
      kind,
      label: `${month}月${n}日 ${text}`,
      icon: CELL_ICONS[kind] ?? '',
      mark: CELL_MARKS[kind] ?? '',
    })
  }
  return { lead, days }
})
</script>

<template>
  <div class="att-cal">
    <div
      class="att-cal__weekdays"
      aria-hidden="true"
    >
      <span
        v-for="w in WEEKDAYS"
        :key="w"
        class="att-cal__wd m3-label-medium"
      >{{ w }}</span>
    </div>
    <div class="att-cal__grid">
      <span
        v-for="i in grid.lead"
        :key="`pad-${i}`"
        class="att-cal__pad"
        aria-hidden="true"
      />
      <button
        v-for="c in grid.days"
        :key="c.date"
        type="button"
        class="att-day"
        :class="[`att-day--${c.kind}`, { 'is-today': c.date === today, 'is-selected': c.date === selectedDate }]"
        :aria-label="c.label"
        :aria-current="c.date === today ? 'date' : undefined"
        :aria-pressed="c.date === selectedDate"
        @click="emit('select', c.date)"
      >
        <span class="att-day__num m3-body-medium">{{ c.day }}</span>
        <span
          class="att-day__mark"
          aria-hidden="true"
        >
          <M3Icon
            v-if="c.icon"
            :name="c.icon"
            :size="16"
          />
          <span
            v-else-if="c.mark"
            class="m3-label-small"
          >{{ c.mark }}</span>
        </span>
      </button>
    </div>
    <div
      class="att-legend"
      role="group"
      aria-label="圖例"
    >
      <span
        v-for="l in LEGEND"
        :key="l.kind"
        class="att-legend__item m3-body-small"
      >
        <span
          class="att-legend__swatch"
          :class="`att-day--${l.kind}`"
          aria-hidden="true"
        >
          <M3Icon
            v-if="CELL_ICONS[l.kind]"
            :name="CELL_ICONS[l.kind] ?? ''"
            :size="16"
          />
          <span
            v-else-if="CELL_MARKS[l.kind]"
            class="m3-label-small"
          >{{ CELL_MARKS[l.kind] }}</span>
        </span>
        <span class="att-legend__label">{{ l.label }}</span>
      </span>
    </div>
  </div>
</template>

<style scoped>
.att-cal__weekdays,
.att-cal__grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 4px;
}

.att-cal__wd {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 32px;
  color: var(--m3-on-surface-variant);
}

.att-cal__pad {
  height: 52px;
}

.att-day {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  min-width: 0;
  height: 52px;
  padding: 0;
  border: none;
  border-radius: var(--m3-shape-small);
  background: transparent;
  color: var(--m3-on-surface);
  font-variant-numeric: tabular-nums;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition:
    background-color var(--m3-dur-short-3) var(--m3-easing-standard),
    color var(--m3-dur-short-3) var(--m3-easing-standard);
}

/* state layer：hover 0.08 / focus 0.12 / pressed 0.12，顏色取 currentColor */
.att-day::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: currentcolor;
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--m3-dur-short-2) var(--m3-easing-standard);
}

.att-day:hover::before {
  opacity: var(--m3-state-hover);
}

.att-day:focus-visible::before {
  opacity: var(--m3-state-focus);
}

.att-day:active::before {
  opacity: var(--m3-state-pressed);
}

.att-day__mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 16px;
}

/* 狀態底色 = StatusPill 同一組 container token；請假用 tertiary 與已離班區分 */
.att-day--present {
  background: var(--m3-primary-container);
  color: var(--m3-on-primary-container);
}

.att-day--left {
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}

.att-day--leave {
  background: var(--m3-tertiary-container);
  color: var(--m3-on-tertiary-container);
}

.att-day--absent {
  background: var(--m3-error-container);
  color: var(--m3-on-error-container);
}

.att-day--expected {
  background: var(--m3-warning-container);
  color: var(--m3-on-warning-container);
}

.att-day--unrecorded {
  background: var(--m3-surface-container-high);
  color: var(--m3-on-surface);
}

.att-day--closed {
  color: var(--m3-on-surface-variant);
}

.att-day--none {
  color: var(--m3-on-surface);
}

.att-day.is-today {
  box-shadow: inset 0 0 0 2px var(--m3-primary);
}

.att-day.is-today .att-day__num {
  font-weight: 700;
}

.att-day.is-selected {
  background: var(--m3-primary);
  color: var(--m3-on-primary);
}

/* 今天同時被選中：實心底 + 內框再加一圈 on-primary，兩種標示都看得到 */
.att-day.is-selected.is-today {
  box-shadow:
    inset 0 0 0 2px var(--m3-primary),
    inset 0 0 0 4px var(--m3-on-primary);
}

.att-day:focus-visible {
  outline: 2px solid var(--m3-on-surface);
  outline-offset: 1px;
}

.att-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--m3-outline-variant);
}

.att-legend__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--m3-on-surface-variant);
}

.att-legend__swatch {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: var(--m3-shape-extra-small);
}

/* 沒有底色的休息 / 無紀錄色塊要有細框才看得到 */
.att-legend__swatch.att-day--closed,
.att-legend__swatch.att-day--none {
  box-shadow: inset 0 0 0 1px var(--m3-outline-variant);
}
</style>
