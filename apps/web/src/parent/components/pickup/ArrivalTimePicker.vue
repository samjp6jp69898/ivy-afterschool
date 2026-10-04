<script setup lang="ts">
/**
 * 預計抵達時間選擇：不指定 / 約 10、20、30 分鐘後 / 自選時間。後端收絕對時間 HH:MM（台北），
 * 相對選項由前端以 now（秒數無條件進位到分鐘）換算。超過 23:59 的相對選項停用。
 * 未傳 now 時每 60 秒重算（unmount 清除）；傳入 now 時以 prop 為準。
 */
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { nowHHMM } from '../../../shared/utils/datetime'
import M3Chip from '../m3/M3Chip.vue'
import M3TextField from '../m3/M3TextField.vue'

type Choice = 'none' | 10 | 20 | 30 | 'custom'

const props = withDefaults(defineProps<{ modelValue: string | null; now?: Date }>(), { now: undefined })

const emit = defineEmits<{ 'update:modelValue': [string | null] }>()

const RELATIVE = [10, 20, 30] as const
const LAST_MINUTE_OF_DAY = 23 * 60 + 59
const TICK_MS = 60_000
const ERROR_TEXT = '預計抵達時間不能早於現在'

const headingId = useId()
const innerNow = ref(new Date())
const currentNow = computed(() => props.now ?? innerNow.value)
let timer: ReturnType<typeof setInterval> | null = null

const choice = ref<Choice>(props.modelValue ? 'custom' : 'none')
const customValue = ref(props.modelValue ?? '')
let lastEmitted: string | null = props.modelValue

function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(':')
  return Number(h) * 60 + Number(m)
}

function toHHMM(total: number): string {
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

const nowMinutes = computed(() => toMinutes(nowHHMM(currentNow.value)))
// 台北為整分鐘時差，UTC 秒數即台北秒數
const roundedNowMinutes = computed(() => nowMinutes.value + (currentNow.value.getUTCSeconds() > 0 ? 1 : 0))

/** 相對選項換算結果；超過 23:59 為 null（停用） */
function relativeTime(minutes: number): string | null {
  const total = roundedNowMinutes.value + minutes
  return total > LAST_MINUTE_OF_DAY ? null : toHHMM(total)
}

const customError = computed(() => {
  if (choice.value !== 'custom' || customValue.value === '') return ''
  return toMinutes(customValue.value) < nowMinutes.value ? ERROR_TEXT : ''
})

const output = computed<string | null>(() => {
  if (choice.value === 'none') return null
  if (choice.value === 'custom') return customValue.value !== '' && !customError.value ? customValue.value : null
  return relativeTime(choice.value)
})

function emitValue(value: string | null): void {
  lastEmitted = value
  emit('update:modelValue', value)
}

function select(next: Choice): void {
  choice.value = next
  if (next === 'custom') customValue.value = relativeTime(10) ?? ''
  emitValue(output.value)
}

function onCustomInput(value: string): void {
  customValue.value = value
  emitValue(output.value)
}

// 時間經過：選中的相對選項被停用就回到不指定，其餘輸出變動時同步 emit
watch(currentNow, () => {
  if (typeof choice.value === 'number' && relativeTime(choice.value) === null) choice.value = 'none'
})

watch(output, (value) => {
  if (value !== lastEmitted) emitValue(value)
})

// 父層把值清掉（例如表單重設）→ 回到不指定
watch(
  () => props.modelValue,
  (value) => {
    if (value === null && lastEmitted !== null) {
      choice.value = 'none'
      customValue.value = ''
      lastEmitted = null
    }
  },
)

onMounted(() => {
  if (props.now !== undefined) return
  timer = setInterval(() => {
    innerNow.value = new Date()
  }, TICK_MS)
})

onBeforeUnmount(() => {
  if (timer !== null) clearInterval(timer)
})

function relativeLabel(minutes: number): string {
  const time = relativeTime(minutes)
  return time ? `約 ${minutes} 分鐘後 · ${time}` : `約 ${minutes} 分鐘後`
}
</script>

<template>
  <div
    class="arrival"
    role="group"
    :aria-labelledby="headingId"
  >
    <p
      :id="headingId"
      class="arrival__title m3-title-small"
    >
      預計抵達時間
    </p>
    <div class="arrival__chips">
      <M3Chip
        variant="filter"
        label="不指定"
        :selected="choice === 'none'"
        @click="select('none')"
      />
      <M3Chip
        v-for="minutes in RELATIVE"
        :key="minutes"
        class="arrival__relative"
        variant="filter"
        :label="relativeLabel(minutes)"
        :selected="choice === minutes"
        :disabled="relativeTime(minutes) === null"
        @click="select(minutes)"
      />
      <M3Chip
        variant="filter"
        label="自選時間"
        :selected="choice === 'custom'"
        @click="select('custom')"
      />
    </div>
    <div
      v-if="choice === 'custom'"
      class="arrival__custom"
    >
      <M3TextField
        :model-value="customValue"
        label="抵達時間"
        type="time"
        supporting-text="清空時視同不指定"
        :error-text="customError"
        @update:model-value="onCustomInput"
      />
    </div>
  </div>
</template>

<style scoped>
.arrival {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.arrival__title {
  margin: 0;
  color: var(--m3-on-surface);
}

.arrival__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 0;
}

.arrival__relative {
  font-variant-numeric: tabular-nums;
}

.arrival__chips :deep(.m3-chip:disabled) {
  opacity: 0.38;
}

.arrival__custom {
  padding-top: 4px;
}
</style>
