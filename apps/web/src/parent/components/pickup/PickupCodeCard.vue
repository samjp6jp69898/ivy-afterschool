<script setup lang="ts">
/**
 * 代理接送碼只顯示一次的卡片（放在不可關閉的 ParentBottomSheet 內）。
 * 接送碼明碼只存在 props：不寫任何 storage、Pinia store 或 console，父層在 done 後自行清除暫存。
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import { formatDateWithWeekday } from '../../../shared/utils/datetime'
import { useSnackbarStore } from '../../stores/snackbar'
import M3Button from '../m3/M3Button.vue'
import M3Icon from '../m3/M3Icon.vue'

const props = defineProps<{ code: string; proxyName: string; serviceDate: string }>()

const emit = defineEmits<{ done: [] }>()

const COPIED_MS = 2000
const GROUP_AFTER = 3

const snackbar = useSnackbarStore()
const copied = ref(false)
let copiedTimer: ReturnType<typeof setTimeout> | null = null

const digits = computed(() => props.code.split(''))
const meta = computed(() => `${props.proxyName} · ${formatDateWithWeekday(props.serviceDate)}`)

function clearCopiedTimer(): void {
  if (copiedTimer !== null) clearTimeout(copiedTimer)
  copiedTimer = null
}

async function copyCode(): Promise<void> {
  try {
    if (!navigator.clipboard) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(props.code)
  } catch {
    snackbar.show('無法自動複製，請手動記下')
    return
  }
  copied.value = true
  clearCopiedTimer()
  copiedTimer = setTimeout(() => {
    copied.value = false
    copiedTimer = null
  }, COPIED_MS)
  snackbar.show('已複製接送碼')
}

onBeforeUnmount(clearCopiedTimer)
</script>

<template>
  <div class="code-card">
    <div class="code-box">
      <div
        class="code"
        role="status"
      >
        <span
          v-for="(digit, index) in digits"
          :key="index"
          class="code__digit"
          :class="{ 'is-group-end': index === GROUP_AFTER - 1 }"
        >{{ digit }}</span>
      </div>
      <p class="code-meta m3-body-large">
        {{ meta }}
      </p>
    </div>

    <M3Button
      variant="tonal"
      :icon="copied ? 'check' : 'content_copy'"
      block
      @click="copyCode"
    >
      {{ copied ? '已複製' : '複製接送碼' }}
    </M3Button>

    <div class="code-warning">
      <M3Icon
        name="warning"
        filled
      />
      <p class="m3-body-medium">
        接送碼只會顯示這一次，關閉後無法再查看。遺失時可以重新產生，舊的接送碼會立即失效。
      </p>
    </div>

    <M3Button
      block
      @click="emit('done')"
    >
      我已記下
    </M3Button>
  </div>
</template>

<style scoped>
.code-card {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.code-box {
  padding: 20px 16px;
  border-radius: var(--m3-shape-large);
  background: var(--m3-primary-container);
  color: var(--m3-on-primary-container);
  text-align: center;
}

.code {
  font-size: 44px;
  font-weight: 500;
  line-height: 52px;
  letter-spacing: 6px;
  font-variant-numeric: tabular-nums;
}

.code__digit {
  display: inline-block;
}

.code__digit.is-group-end {
  margin-right: 14px;
}

.code-meta {
  margin: 8px 0 0;
  overflow-wrap: anywhere;
}

.code-warning {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 16px;
  border-radius: var(--m3-shape-medium);
  background: var(--m3-error-container);
  color: var(--m3-on-error-container);
}

.code-warning p {
  margin: 0;
}
</style>
