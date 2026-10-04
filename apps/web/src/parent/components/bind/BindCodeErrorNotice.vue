<script setup lang="ts">
/**
 * 綁定碼錯誤提示：role="alert" 提示框（訊息 + 下一步）與其下的按鈕列（按鈕不放在 alert 內）。
 * 錯誤碼對照 BACKEND-055 / 056 / 057；其他 / null 只顯示 fallbackMessage，不渲染「下一步」。
 */
import { computed } from 'vue'
import M3Button from '../m3/M3Button.vue'
import M3Icon from '../m3/M3Icon.vue'

type Tone = 'error' | 'warning' | 'info'

interface Entry {
  message: string
  next: string
  tone: Tone
}

const props = withDefaults(
  defineProps<{ code: string | null; fallbackMessage?: string; orgPhone?: string | null }>(),
  { fallbackMessage: '', orgPhone: null },
)

const emit = defineEmits<{ retry: [] }>()

const TABLE: Record<string, Entry> = {
  binding_code_invalid: { message: '綁定碼不正確', next: '請確認是否輸入正確（注意數字 0 與英文 O）', tone: 'error' },
  binding_code_expired: { message: '綁定碼已過期', next: '請向安親班索取新的綁定碼', tone: 'error' },
  binding_code_used: {
    message: '綁定碼已被使用',
    next: '每組綁定碼只能使用一次，請向安親班索取新的綁定碼',
    tone: 'error',
  },
  guardian_already_bound: {
    message: '這組綁定碼對應的家長已綁定其他 LINE 帳號',
    next: '如需更換 LINE 帳號，請聯絡安親班解除綁定',
    tone: 'error',
  },
  already_bound_to_student: { message: '您已經綁定過這位小孩', next: '不需要重複綁定', tone: 'info' },
  too_many_attempts: { message: '嘗試次數過多', next: '請稍後再試', tone: 'warning' },
  parent_disabled: { message: '您的家長帳號已停用', next: '請聯絡安親班', tone: 'error' },
}

const ICONS: Record<Tone, string> = { error: 'error', warning: 'hourglass_top', info: 'info' }
const CONTACT_CODES = new Set(['binding_code_expired', 'binding_code_used', 'guardian_already_bound', 'parent_disabled'])
// 第一個分機符號之後都不撥
const EXTENSION_MARK = /#|轉|分機|ext|x/i

const entry = computed<Entry | null>(() => (props.code ? (TABLE[props.code] ?? null) : null))
const message = computed(() => entry.value?.message ?? (props.fallbackMessage || '綁定失敗，請稍後再試'))
const tone = computed<Tone>(() => entry.value?.tone ?? 'error')

const phone = computed(() => (props.orgPhone ?? '').trim())
const showCall = computed(() => !!props.code && CONTACT_CODES.has(props.code) && phone.value !== '')
const telHref = computed(() => {
  const main = phone.value.split(EXTENSION_MARK)[0] ?? ''
  const trimmed = main.trim()
  const digits = trimmed.replace(/\D/g, '')
  return `tel:${trimmed.startsWith('+') ? '+' : ''}${digits}`
})
</script>

<template>
  <div class="bind-error">
    <div
      class="notice"
      :data-tone="tone"
      role="alert"
    >
      <M3Icon
        class="notice__icon"
        :name="ICONS[tone]"
        filled
      />
      <div class="notice__body">
        <p class="notice__message m3-title-medium">
          {{ message }}
        </p>
        <template v-if="entry">
          <p class="notice__label m3-label-medium">
            下一步
          </p>
          <p class="notice__next m3-body-medium">
            {{ entry.next }}
          </p>
        </template>
      </div>
    </div>
    <div class="bind-error__actions">
      <a
        v-if="showCall"
        class="call-link m3-label-large"
        :href="telHref"
      >
        <M3Icon
          name="call"
          :size="18"
        />
        <span>聯絡安親班 {{ orgPhone }}</span>
      </a>
      <M3Button
        variant="outlined"
        icon="edit"
        block
        @click="emit('retry')"
      >
        重新輸入
      </M3Button>
    </div>
  </div>
</template>

<style scoped>
.notice {
  display: flex;
  gap: 12px;
  padding: 16px;
  border-radius: var(--m3-shape-medium);
}

.notice[data-tone='error'] {
  background: var(--m3-error-container);
  color: var(--m3-on-error-container);
}

.notice[data-tone='warning'] {
  background: var(--m3-warning-container);
  color: var(--m3-on-warning-container);
}

.notice[data-tone='info'] {
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}

.notice__icon {
  flex: none;
}

.notice__body {
  flex: 1;
  min-width: 0;
}

.notice__message,
.notice__label,
.notice__next {
  margin: 0;
  overflow-wrap: anywhere;
}

.notice__label {
  margin-top: 8px;
}

.bind-error__actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 16px;
}

.call-link {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  box-sizing: border-box;
  width: 100%;
  min-height: 48px;
  padding: 8px 24px 8px 16px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
  text-align: center;
  text-decoration: none;
  overflow-wrap: anywhere;
  -webkit-tap-highlight-color: transparent;
}

.call-link:hover {
  filter: brightness(0.96);
}

.call-link:focus-visible {
  outline: 2px solid var(--m3-primary);
  outline-offset: 2px;
}
</style>
