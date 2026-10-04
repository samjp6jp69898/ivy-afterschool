<script setup lang="ts">
// FRONTEND-157：設定預計可接送時間與給家長的說明。設計稿：docs/mockups/page-homework-board.html。
// - 以 FormDialog sm 呈現；送出只 emit submit，由父層呼叫 API、成功後關閉。
// - 時間選單 5 分一格，起點為「開啟當下」的現在時間無條件進位到 5 分，到 21:00；快捷鈕以「點擊當下」為基準。
// - 儲存 = 與開啟時的表單快照不同；dirty 時關閉會先確認（FormDialog）。
import { computed, ref, watch } from 'vue'
import { Bell } from '@element-plus/icons-vue'
import FormDialog from '@/components/common/FormDialog.vue'
import type { HHMM, ISODateTime } from '@/shared/types/api'
import { formatTime, nowHHMM } from '@/shared/utils/datetime'

/** 與 api/homework.ts 的 Progress 相容的子集（本元件只用到這些欄位） */
export interface ReadyEtaProgress {
  ready_eta: HHMM | null
  note: string | null
  eta_updated_at: ISODateTime | null
  eta_updated_by_name: string | null
}

const props = defineProps<{
  modelValue: boolean
  studentName: string
  progress: ReadyEtaProgress
  loading: boolean
  error: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [body: { ready_eta: string | null; note: string | null }]
}>()

const STEP_MINUTES = 5
const END_MINUTES = 21 * 60
const QUICK_MINUTES = [15, 30, 60] as const

const readyEta = ref<string | null>(null)
const note = ref('')
const snapshot = ref('')
const openedAtMinutes = ref(0)

function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number)
  return (h ?? 0) * 60 + (m ?? 0)
}

function toHHMM(minutes: number): HHMM {
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
}

function roundUp(minutes: number): number {
  return Math.ceil(minutes / STEP_MINUTES) * STEP_MINUTES
}

function formState(): string {
  return JSON.stringify([readyEta.value, note.value])
}

function reset(): void {
  readyEta.value = props.progress.ready_eta
  note.value = props.progress.note ?? ''
  snapshot.value = formState()
  openedAtMinutes.value = toMinutes(nowHHMM())
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) reset()
  },
  { immediate: true },
)

const timeStart = computed(() => toHHMM(roundUp(openedAtMinutes.value)))
const changed = computed(() => formState() !== snapshot.value)
const lastUpdater = computed(() =>
  props.progress.eta_updated_by_name
    ? `上次由 ${props.progress.eta_updated_by_name} 於 ${formatTime(props.progress.eta_updated_at)} 設定`
    : '',
)

/** 快捷鈕的結果超過 21:00（選單上限）就停用 */
function quickDisabled(minutes: number): boolean {
  return roundUp(openedAtMinutes.value + minutes) > END_MINUTES
}

function applyQuick(minutes: number): void {
  const target = roundUp(toMinutes(nowHHMM()) + minutes)
  if (target > END_MINUTES) return
  readyEta.value = toHHMM(target)
}

function submit(): void {
  emit('submit', { ready_eta: readyEta.value, note: note.value.trim() || null })
}
</script>

<template>
  <FormDialog
    :model-value="modelValue"
    :title="`預計可接送時間：${studentName}`"
    size="sm"
    :loading="loading"
    :dirty="changed"
    :submit-disabled="!changed"
    @update:model-value="emit('update:modelValue', $event)"
    @submit="submit"
  >
    <el-alert
      v-if="error"
      class="eta-dialog__error"
      type="error"
      :closable="false"
      show-icon
      :title="error"
    />
    <el-form label-position="top">
      <el-form-item label="預計可接送時間">
        <el-time-select
          v-model="readyEta"
          class="eta-dialog__full"
          :start="timeStart"
          end="21:00"
          step="00:05"
          placeholder="選擇時間"
          :clearable="false"
        />
        <div class="eta-dialog__quick">
          <el-button
            v-for="minutes in QUICK_MINUTES"
            :key="minutes"
            :disabled="quickDisabled(minutes)"
            @click="applyQuick(minutes)"
          >
            +{{ minutes }} 分
          </el-button>
          <span class="eta-dialog__spacer" />
          <el-button
            v-if="readyEta"
            text
            type="danger"
            @click="readyEta = null"
          >
            清除預計時間
          </el-button>
        </div>
      </el-form-item>
      <el-form-item label="給家長的說明">
        <el-input
          v-model="note"
          type="textarea"
          :rows="2"
          maxlength="100"
          show-word-limit
          placeholder="例如：數學訂正中，預計 17:30 完成"
        />
      </el-form-item>
    </el-form>
    <div
      v-if="lastUpdater"
      class="eta-dialog__last"
    >
      {{ lastUpdater }}
    </div>
    <div class="eta-dialog__hint">
      <el-icon><Bell /></el-icon>儲存後會通知家長
    </div>
  </FormDialog>
</template>

<style scoped>
.eta-dialog__error {
  margin-bottom: 16px;
}

.eta-dialog__full {
  width: 100%;
}

.eta-dialog__quick {
  display: flex;
  gap: 8px;
  width: 100%;
  margin-top: 8px;
}

.eta-dialog__quick :deep(.el-button) {
  min-width: 72px;
  height: 44px;
}

.eta-dialog__quick :deep(.el-button + .el-button) {
  margin-left: 0;
}

.eta-dialog__spacer {
  flex: 1;
}

.eta-dialog__last {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.eta-dialog__hint {
  display: flex;
  gap: 4px;
  align-items: center;
  margin-top: 4px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
