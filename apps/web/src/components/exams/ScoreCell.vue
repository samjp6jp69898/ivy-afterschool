<script setup lang="ts">
// FRONTEND-236：單一成績格（分數、缺考、驗證、儲存狀態）。設計稿：docs/mockups/component-exams.html。
// - 允許空白（未填）、0~fullScore、最多一位小數；不合法即時紅框 + tooltip / title，不 emit。
// - 合法且與 props 不同才在 blur / Enter 時 emit；輸入「缺」或「-」等同勾選缺考。
// - saved 標記在 state 轉 idle 時淡出（轉換時機由 useScoreAutosave FRONTEND-237 決定）。
import { Check, Loading, WarningFilled } from '@element-plus/icons-vue'
import { computed, ref, watch } from 'vue'

type CellState = 'idle' | 'dirty' | 'saving' | 'saved' | 'error'
interface CellValue {
  score: number | null
  is_absent: boolean
}

const props = withDefaults(
  defineProps<{
    score: number | null
    isAbsent: boolean
    fullScore: number
    state: CellState
    errorMessage?: string
    readonly: boolean
  }>(),
  { errorMessage: '' },
)

const emit = defineEmits<{ change: [value: CellValue] }>()

const ABSENT_INPUTS = new Set(['缺', '-'])

const formatScore = (n: number | null): string => (n === null ? '' : String(n))

const text = ref(formatScore(props.score))

watch(
  () => [props.score, props.isAbsent] as const,
  () => {
    text.value = formatScore(props.score)
  },
)

type Parsed = { ok: true; value: CellValue } | { ok: false; message: string }

function parse(raw: string): Parsed {
  const t = raw.trim()
  if (t === '') return { ok: true, value: { score: null, is_absent: false } }
  if (ABSENT_INPUTS.has(t)) return { ok: true, value: { score: null, is_absent: true } }
  if (/^\d+\.\d{2,}$/.test(t)) return { ok: false, message: '最多一位小數' }
  if (!/^\d+(\.\d)?$/.test(t) || Number(t) > props.fullScore) {
    return { ok: false, message: `需介於 0~${props.fullScore}` }
  }
  return { ok: true, value: { score: Number(t), is_absent: false } }
}

const localError = computed(() => {
  const parsed = parse(text.value)
  return parsed.ok ? '' : parsed.message
})

// 本地驗證錯誤優先於後端錯誤
const tip = computed(() => localError.value || (props.state === 'error' ? props.errorMessage : ''))

function commit(): void {
  const parsed = parse(text.value)
  if (!parsed.ok) return
  if (parsed.value.is_absent) {
    text.value = ''
    emit('change', parsed.value)
    return
  }
  if (parsed.value.score === props.score && !props.isAbsent) return
  emit('change', parsed.value)
}

function onAbsentChange(value: string | number | boolean): void {
  text.value = ''
  emit('change', { score: null, is_absent: value === true })
}

const readonlyText = computed(() => {
  if (props.isAbsent) return '缺考'
  return props.score === null ? '—' : formatScore(props.score)
})
</script>

<template>
  <span
    v-if="readonly"
    class="score-cell__text"
    :class="{ 'is-absent': isAbsent, 'is-empty': !isAbsent && score === null }"
  >{{ readonlyText }}</span>
  <div
    v-else
    class="score-cell"
    :class="{ 'is-error': state === 'error', 'is-invalid': !!localError }"
    :title="tip || undefined"
  >
    <el-tooltip
      :content="tip"
      :disabled="!tip"
      placement="top"
    >
      <el-input
        v-model="text"
        class="score-cell__input"
        inputmode="decimal"
        :disabled="isAbsent"
        :placeholder="isAbsent ? '缺考' : ''"
        @blur="commit"
        @keyup.enter="commit"
      />
    </el-tooltip>
    <el-checkbox
      class="score-cell__absent"
      :model-value="isAbsent"
      size="small"
      @change="onAbsentChange"
    >
      缺
    </el-checkbox>
    <span
      v-if="state === 'dirty'"
      class="score-cell__dirty"
      aria-label="尚未儲存"
      data-test="mark-dirty"
    />
    <span
      v-else-if="state === 'saving'"
      class="score-cell__mark"
      data-test="mark-saving"
    >
      <el-icon
        class="score-cell__spin"
        aria-label="儲存中"
      ><Loading /></el-icon>
    </span>
    <transition name="score-cell-fade">
      <span
        v-if="state === 'saved'"
        class="score-cell__mark"
        data-test="mark-saved"
      >
        <el-icon
          class="score-cell__ok"
          aria-label="已儲存"
        ><Check /></el-icon>
      </span>
    </transition>
    <span
      v-if="state === 'error' && !localError"
      class="score-cell__mark"
      data-test="mark-error"
    >
      <el-icon class="score-cell__err"><WarningFilled /></el-icon>
    </span>
  </div>
</template>

<style scoped>
.score-cell {
  position: relative;
  display: inline-flex;
  gap: 6px;
  align-items: center;
}

.score-cell__input {
  width: 64px;
}

.score-cell__input :deep(.el-input__inner) {
  font-variant-numeric: tabular-nums;
  text-align: center;
}

.score-cell.is-error :deep(.el-input__wrapper),
.score-cell.is-invalid :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}

.score-cell__absent {
  height: 24px;
}

.score-cell__absent :deep(.el-checkbox__label) {
  padding-left: 4px;
  font-size: 13px;
}

/* 狀態標記貼在輸入框右上角，不推動版面 */
.score-cell__mark {
  position: absolute;
  top: -5px;
  left: 54px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  pointer-events: none;
}

.score-cell__dirty {
  position: absolute;
  top: 1px;
  left: 53px;
  width: 0;
  height: 0;
  pointer-events: none;
  border-top: 10px solid var(--el-color-warning);
  border-left: 10px solid transparent;
  border-top-right-radius: 3px;
}

.score-cell__spin {
  font-size: 13px;
  color: var(--el-color-primary);
  background: var(--el-bg-color);
  border-radius: 50%;
  animation: score-cell-spin 1s linear infinite;
}

.score-cell__ok {
  padding: 1px;
  font-size: 13px;
  color: var(--el-color-white);
  background: var(--el-color-success);
  border-radius: 50%;
}

.score-cell__err {
  font-size: 14px;
  color: var(--el-color-danger);
  background: var(--el-bg-color);
  border-radius: 50%;
}

.score-cell-fade-leave-active {
  transition: opacity 0.4s ease;
}

.score-cell-fade-leave-to {
  opacity: 0;
}

@keyframes score-cell-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .score-cell__spin {
    animation: none;
  }

  .score-cell-fade-leave-active {
    transition: none;
  }
}

.score-cell__text {
  display: inline-block;
  min-width: 40px;
  font-variant-numeric: tabular-nums;
  text-align: center;
}

.score-cell__text.is-absent {
  color: var(--el-color-warning-dark-2);
}

.score-cell__text.is-empty {
  color: var(--el-text-color-placeholder);
}
</style>
