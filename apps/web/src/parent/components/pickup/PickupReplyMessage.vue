<script setup lang="ts">
/**
 * 接送請求的回覆（預計可接送時間與訊息）。純展示、不可互動。
 * 完整模式由上而下：來源小標 → ETA 大字 → 訊息泡泡 → 「HH:MM 回覆」，各自有值才渲染；
 * ETA 與訊息都沒有 → 等待樣式。compact（首頁今日狀態卡）只一行 + 來源小標，不顯示時間。
 * 外層 aria-live="polite"，回覆被 ws 更新時原地替換內容，螢幕閱讀器會念出新回覆。
 * 訊息一律以文字插值渲染（不用 v-html），換行靠 white-space: pre-wrap 保留。
 */
import { computed } from 'vue'
import { formatTime } from '@/shared/utils/datetime'
import type { ParentPickupRequest } from '../../api/pickupRequests'
import M3Icon from '../m3/M3Icon.vue'

const props = withDefaults(
  defineProps<{
    request: Pick<ParentPickupRequest, 'reply_ready_eta' | 'reply_message' | 'reply_source'> & {
      replied_at?: string | null
    }
    compact?: boolean
  }>(),
  { compact: false },
)

const SOURCES = {
  auto: { icon: 'smart_toy', label: '系統自動回覆' },
  staff: { icon: 'person', label: '老師回覆' },
} as const

const WAITING_TEXT = '已通知老師，稍後回覆預計時間'

const eta = computed(() => props.request.reply_ready_eta || null)
const etaText = computed(() => (eta.value ? `預計 ${eta.value} 可接送` : ''))
const message = computed(() => props.request.reply_message ?? '')
// 只有空白的訊息視同沒有訊息，不畫空泡泡
const hasMessage = computed(() => message.value.trim() !== '')
const waiting = computed(() => !eta.value && !hasMessage.value)

const source = computed(() => {
  const key = props.request.reply_source
  return key ? (SOURCES[key] ?? null) : null
})

// 系統自動回覆有 ETA 時，後端 reply_message 固定為「預計 HH:MM 可接送\n{員工說明}」（BACKEND-403）：
// 第一行（trim 後）與 ETA 大字相同就省略該行，剩下的內容 trim 後為空字串就不畫泡泡。
const bubbleText = computed(() => {
  if (!hasMessage.value) return ''
  if (!eta.value) return message.value
  const [first = '', ...rest] = message.value.split('\n')
  return first.trim() === etaText.value ? rest.join('\n').trim() : message.value
})

const compactText = computed(() => etaText.value || message.value.trim().split('\n')[0]?.trim() || '')

const repliedAt = computed(() => (props.request.replied_at ? formatTime(props.request.replied_at) : ''))
</script>

<template>
  <div
    class="prm"
    :class="{ 'is-compact': compact }"
    aria-live="polite"
  >
    <template v-if="waiting">
      <div class="prm-wait">
        <M3Icon
          class="prm-wait__icon"
          name="hourglass_top"
          :size="compact ? 18 : 24"
        />
        <span
          class="prm-wait__text"
          :class="compact ? 'm3-body-medium' : 'm3-body-large'"
        >{{ WAITING_TEXT }}</span>
        <span
          v-if="compact"
          class="prm-spinner"
          aria-hidden="true"
        />
      </div>
      <div
        v-if="!compact"
        class="prm-progress"
        role="progressbar"
        aria-label="等待老師回覆"
      >
        <span class="prm-progress__bar" />
      </div>
    </template>
    <template v-else-if="compact">
      <p class="prm-compact__text m3-title-medium">
        {{ compactText }}
      </p>
      <p
        v-if="source"
        class="prm-source m3-label-small"
      >
        <M3Icon
          :name="source.icon"
          :size="14"
        />{{ source.label }}
      </p>
    </template>
    <template v-else>
      <p
        v-if="source"
        class="prm-source m3-label-medium"
      >
        <M3Icon
          :name="source.icon"
          :size="16"
        />{{ source.label }}
      </p>
      <p
        v-if="eta"
        class="prm-eta m3-headline-small"
      >
        預計 <span class="prm-eta__time">{{ eta }}</span> 可接送
      </p>
      <p
        v-if="bubbleText"
        class="prm-bubble m3-body-large"
        data-testid="reply-bubble"
      >
        {{ bubbleText }}
      </p>
      <p
        v-if="repliedAt"
        class="prm-time m3-body-small"
      >
        {{ repliedAt }} 回覆
      </p>
    </template>
  </div>
</template>

<style scoped>
.prm {
  min-width: 0;
}

.prm-source {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 0;
  color: var(--m3-on-surface-variant);
}

.prm-eta {
  margin: 4px 0 0;
  color: var(--m3-on-surface);
}

.prm-eta__time {
  color: var(--m3-primary);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

/* 左側發話的對話泡泡：左上 4px、其餘 16px；長訊息完整顯示，不截斷 */
.prm-bubble {
  max-width: 100%;
  margin: 8px 0 0;
  padding: 12px 16px;
  border-radius: var(--m3-shape-extra-small) var(--m3-shape-large) var(--m3-shape-large) var(--m3-shape-large);
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.prm-time {
  margin: 4px 0 0;
  color: var(--m3-on-surface-variant);
  font-variant-numeric: tabular-nums;
}

.prm-wait {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--m3-on-surface);
}

.prm-wait__icon {
  color: var(--m3-on-surface-variant);
}

.prm-wait__text {
  flex: 1;
  min-width: 0;
}

/* indeterminate 線性進度條 */
.prm-progress {
  position: relative;
  height: 4px;
  margin-top: 12px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-surface-container-highest);
  overflow: hidden;
}

.prm-progress__bar {
  position: absolute;
  top: 0;
  bottom: 0;
  left: -40%;
  width: 40%;
  border-radius: inherit;
  background: var(--m3-primary);
  animation: prm-indeterminate 1.6s var(--m3-easing-standard) infinite;
}

@keyframes prm-indeterminate {
  from {
    left: -40%;
  }

  to {
    left: 100%;
  }
}

/* reduced-motion：base.css 會把動畫縮成一次性瞬間，長條會消失，改為靜止的淡色長條 */
@media (prefers-reduced-motion: reduce) {
  .prm-progress__bar {
    left: 0;
    width: 100%;
    animation: none;
    opacity: 0.38;
  }
}

/* compact：一行省略 + 來源小標 */
.prm.is-compact .prm-source {
  margin-top: 2px;
}

.prm-compact__text {
  margin: 0;
  overflow: hidden;
  color: var(--m3-on-surface);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.prm.is-compact .prm-wait__text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.prm-spinner {
  flex: none;
  width: 16px;
  height: 16px;
  border: 2px solid currentcolor;
  border-right-color: transparent;
  border-radius: 50%;
  color: var(--m3-primary);
  animation: prm-spin 0.8s linear infinite;
}

@keyframes prm-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
