<script setup lang="ts">
/**
 * 接送進度步驟（純展示，垂直步驟條）：已送出 → 老師已確認 → 我已到達 → 已接走。
 * 完成判定：API 沒有 acknowledged_at，「老師已確認」不顯示時間；後端允許 pending 直接到 arrived，
 * 所以 arrived_at 有值也視為老師已確認；completed 但 arrived_at 為 null（家長沒按「我到了」就交付）
 * 時「我已到達」仍視為完成、不顯示時間，避免出現第 4 步完成、第 3 步未完成的斷層。
 * 目前步驟 = 非終態時第一個未完成的步驟（aria-current="step"，名稱下方加提示）；
 * cancelled / expired 沒有目前步驟，未完成步驟降為 38% 透明度，步驟條下方補一行結束說明。
 * 「我到了」「取消」按鈕屬於 PickupView，本元件不可互動。
 */
import { computed } from 'vue'
import { PICKUP_OPEN_STATUSES } from '@/shared/types/api'
import { formatTime } from '@/shared/utils/datetime'
import type { ParentPickupRequest } from '../../api/pickupRequests'
import M3Icon from '../m3/M3Icon.vue'

const props = defineProps<{ request: ParentPickupRequest }>()

type StepKey = 'sent' | 'ack' | 'arrived' | 'completed'

interface Step {
  key: StepKey
  label: string
  done: boolean
  current: boolean
  /** 與下一步之間的連接線：下一步已完成時為 primary */
  lineDone: boolean
  time: string
  sub: string
}

const CURRENT_HINTS: Partial<Record<StepKey, string>> = {
  ack: '等待老師確認',
  arrived: '抵達安親班門口後請按「我到了」',
  completed: '老師正在帶小孩出來',
}

const steps = computed<Step[]>(() => {
  const r = props.request
  const completed = r.status === 'completed'
  const ackDone = r.status === 'acknowledged' || r.status === 'arrived' || completed || !!r.arrived_at
  const arrivedDone = !!r.arrived_at || completed
  const base = [
    { key: 'sent', label: '已送出', done: true, time: formatTime(r.created_at), sub: '' },
    { key: 'ack', label: '老師已確認', done: ackDone, time: '', sub: '' },
    {
      key: 'arrived',
      label: '我已到達',
      done: arrivedDone,
      time: r.arrived_at ? formatTime(r.arrived_at) : '',
      sub: '',
    },
    {
      key: 'completed',
      label: '已接走',
      done: completed,
      time: completed && r.completed_at ? formatTime(r.completed_at) : '',
      sub: completed && r.picked_up_by_name ? `由 ${r.picked_up_by_name} 接走` : '',
    },
  ] satisfies Pick<Step, 'key' | 'label' | 'done' | 'time' | 'sub'>[]
  const isOpen = (PICKUP_OPEN_STATUSES as readonly string[]).includes(r.status)
  const currentIndex = isOpen ? base.findIndex((s) => !s.done) : -1
  return base.map((s, i) => ({
    ...s,
    current: i === currentIndex,
    lineDone: base[i + 1]?.done ?? false,
    sub: s.sub || (i === currentIndex ? (CURRENT_HINTS[s.key] ?? '') : ''),
  }))
})

// 狀態不只靠顏色：每步名稱後附視覺隱藏的完成 / 進行中 / 未完成
function stateText(step: Step): string {
  if (step.done) return '已完成'
  return step.current ? '進行中' : '未完成'
}

const ended = computed(() => props.request.status === 'cancelled' || props.request.status === 'expired')

const endedIcon = computed(() => (props.request.status === 'cancelled' ? 'cancel' : 'timer_off'))

const endedText = computed(() => {
  const r = props.request
  if (r.status === 'cancelled') return r.cancelled_at ? `已取消（${formatTime(r.cancelled_at)}）` : '已取消'
  if (r.status === 'expired') return '接送請求已逾時自動結束'
  return ''
})
</script>

<template>
  <div
    class="pps"
    :class="{ 'is-ended': ended }"
  >
    <ol
      class="pps-list"
      aria-label="接送進度"
    >
      <li
        v-for="s in steps"
        :key="s.key"
        class="pps-step"
        :class="{ 'is-done': s.done, 'is-current': s.current, 'is-line-done': s.lineDone }"
        :aria-current="s.current ? 'step' : undefined"
      >
        <span
          class="pps-marker"
          aria-hidden="true"
        >
          <M3Icon
            v-if="s.done"
            name="check"
            :size="16"
          />
        </span>
        <div class="pps-body">
          <div class="pps-label m3-body-large">
            {{ s.label }}<span class="visually-hidden">（{{ stateText(s) }}）</span>
          </div>
          <div
            v-if="s.sub"
            class="pps-sub m3-body-medium"
          >
            {{ s.sub }}
          </div>
        </div>
        <span
          v-if="s.time"
          class="pps-time m3-body-medium"
        >{{ s.time }}</span>
      </li>
    </ol>
    <div
      v-if="ended"
      class="pps-ended m3-body-medium"
    >
      <M3Icon
        class="pps-ended__icon"
        :name="endedIcon"
      /><span class="pps-ended__text">{{ endedText }}</span>
    </div>
  </div>
</template>

<style scoped>
.pps-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.pps-step {
  position: relative;
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr) auto;
  column-gap: 12px;
  padding-bottom: 20px;
}

.pps-step:last-child {
  padding-bottom: 0;
}

/* 標記之間的連接線 */
.pps-step::before {
  content: '';
  position: absolute;
  top: 28px;
  bottom: 4px;
  left: 11px;
  width: 2px;
  border-radius: 1px;
  background: var(--m3-outline-variant);
}

.pps-step:last-child::before {
  display: none;
}

.pps-step.is-line-done::before {
  background: var(--m3-primary);
}

.pps-marker {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 2px solid var(--m3-outline-variant);
  border-radius: var(--m3-shape-full);
  background: var(--m3-surface);
  color: var(--m3-on-primary);
}

.pps-step.is-done .pps-marker {
  border-color: var(--m3-primary);
  background: var(--m3-primary);
}

.pps-step.is-current .pps-marker {
  border-color: var(--m3-primary);
}

.pps-step.is-current .pps-marker::after {
  content: '';
  width: 10px;
  height: 10px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-primary);
}

.pps-body {
  min-width: 0;
  padding-top: 2px;
}

.pps-label {
  color: var(--m3-on-surface-variant);
}

.pps-step.is-done .pps-label {
  color: var(--m3-on-surface);
}

.pps-step.is-current .pps-label {
  color: var(--m3-on-surface);
  font-weight: 500;
}

.pps-sub {
  margin-top: 2px;
  color: var(--m3-on-surface-variant);
  overflow-wrap: anywhere;
}

.pps-step.is-current .pps-sub {
  color: var(--m3-primary);
}

.pps-time {
  padding-top: 2px;
  color: var(--m3-on-surface-variant);
  font-variant-numeric: tabular-nums;
}

/* 終態：未完成的步驟（標記與文字）降階，已完成的保持原色 */
.pps.is-ended .pps-step:not(.is-done) .pps-marker,
.pps.is-ended .pps-step:not(.is-done) .pps-body {
  opacity: 0.38;
}

.pps-ended {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
  padding: 12px 16px;
  border-radius: var(--m3-shape-medium);
  background: var(--m3-surface-container-high);
  color: var(--m3-on-surface);
}

.pps-ended__icon {
  color: var(--m3-on-surface-variant);
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
