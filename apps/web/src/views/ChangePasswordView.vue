<script setup lang="ts">
// FRONTEND-048：修改密碼（含首次登入 / 被重設後的強制修改）。移植 ivy FE:src/views/ChangePasswordView.vue 的表單與
// FE:src/utils/passwordRules.ts 的前端檢查，規則改為本專案（BACKEND-032：≥ 10 碼、含英文與數字）。
// 設計稿：docs/mockups/page-change-password.html。路由為 blank layout（強制模式不能讓人點側欄離開），本元件撐滿整頁置中。
import { ArrowLeft, CircleCheckFilled, CircleCloseFilled, Key, Remove } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { firstAllowedPath } from '@/router/authGuard'
import { isApiError } from '@/shared/types/api'
import { errorCode, errorMessage } from '@/shared/utils/errorMessage'
import { useAuthStore } from '@/stores/auth'

type Field = 'current' | 'next' | 'confirm'
type RuleState = 'pending' | 'ok' | 'fail'

/** 前端能檢查的規則；「不可等於帳號」「常見弱密碼」由後端以 weak_password 回覆 */
const PASSWORD_RULES = [
  { key: 'length', label: '至少 10 碼', test: (v: string) => v.length >= 10 },
  { key: 'letter', label: '含英文字母', test: (v: string) => /[A-Za-z]/.test(v) },
  { key: 'digit', label: '含數字', test: (v: string) => /\d/.test(v) },
] as const

const RULE_SR_TEXT: Record<RuleState, string> = { pending: '', ok: '（已符合）', fail: '（未符合）' }
const MISMATCH = '兩次輸入的新密碼不一致'
const NETWORK_MESSAGE = '無法連線伺服器，請稍後再試'
const NETWORK_CODES = new Set(['network_error', 'timeout'])

const auth = useAuthStore()
const router = useRouter()

const forced = computed(() => auth.mustChangePassword)
const form = reactive<Record<Field, string>>({ current: '', next: '', confirm: '' })
const errors = reactive<Record<Field, string>>({ current: '', next: '', confirm: '' })
/** 確認欄離開過（或送出過）才開始比對 */
const confirmTouched = ref(false)
const loading = ref(false)

const rules = computed(() =>
  PASSWORD_RULES.map((rule) => {
    const state: RuleState = !form.next ? 'pending' : rule.test(form.next) ? 'ok' : 'fail'
    return { key: rule.key, label: rule.label, state }
  }),
)

function checkConfirm(): void {
  errors.confirm = form.confirm && form.confirm !== form.next ? MISMATCH : ''
}

// 欄位錯誤在使用者修改該欄後清掉；確認欄比對過之後隨兩欄即時更新
watch(
  () => form.current,
  () => {
    errors.current = ''
  },
)
watch(
  () => form.next,
  () => {
    errors.next = ''
    if (confirmTouched.value) checkConfirm()
  },
)
watch(
  () => form.confirm,
  () => {
    if (confirmTouched.value) checkConfirm()
  },
)

function onConfirmBlur(): void {
  confirmTouched.value = true
  checkConfirm()
}

function validate(): boolean {
  errors.current = form.current ? '' : '請輸入目前密碼'
  if (!form.next) errors.next = '請輸入新密碼'
  else if (!PASSWORD_RULES.every((rule) => rule.test(form.next))) errors.next = '新密碼不符合規則'
  else if (form.next === form.current) errors.next = '新密碼不可與目前密碼相同'
  else errors.next = ''
  confirmTouched.value = true
  errors.confirm = form.confirm ? '' : '請再次輸入新密碼'
  if (form.confirm) checkConfirm()
  return !errors.current && !errors.next && !errors.confirm
}

function homePath(): string {
  return firstAllowedPath(auth.permissions) ?? '/forbidden'
}

/** weak_password 的 details.reasons（BACKEND-032 繁中條列原文）以「、」串接；缺漏時回 null */
function weakReasons(err: unknown): string | null {
  if (!isApiError(err) || err.details === null || typeof err.details !== 'object') return null
  const reasons = (err.details as { reasons?: unknown }).reasons
  if (!Array.isArray(reasons)) return null
  const texts = reasons.filter((r): r is string => typeof r === 'string' && r !== '')
  return texts.length > 0 ? texts.join('、') : null
}

function applyError(err: unknown): void {
  const code = errorCode(err)
  if (code === 'current_password_incorrect') errors.current = '目前密碼不正確'
  else if (code === 'password_unchanged') errors.next = '新密碼不可與目前密碼相同'
  else if (code === 'weak_password') errors.next = weakReasons(err) ?? errorMessage(err, '新密碼不符合規則')
  else if (code !== null && NETWORK_CODES.has(code)) ElMessage.error(NETWORK_MESSAGE)
  else ElMessage.error(errorMessage(err, '密碼更新失敗，請稍後再試'))
}

async function submit(): Promise<void> {
  if (loading.value || !validate()) return
  loading.value = true
  try {
    await auth.changePassword(form.current, form.next)
    ElMessage.success('密碼已更新')
    await router.replace(homePath())
  } catch (err) {
    applyError(err)
  } finally {
    loading.value = false
  }
}

/** 有站內上一頁才 router.back()，否則回第一個有權限的頁 */
function goBack(): void {
  if (typeof router.options.history.state.back === 'string') router.back()
  else void router.replace(homePath())
}

async function logout(): Promise<void> {
  await auth.logout()
  await router.replace('/login')
}
</script>

<template>
  <div class="cp-page">
    <main
      class="cp"
      aria-labelledby="cp-title"
    >
      <header class="cp__head">
        <span
          class="cp__icon"
          aria-hidden="true"
        >
          <el-icon><Key /></el-icon>
        </span>
        <div>
          <h1
            id="cp-title"
            class="cp__title"
          >
            修改密碼
          </h1>
          <p class="cp__account">
            {{ auth.user?.display_name }}（帳號 <span class="cp__mono">{{ auth.user?.username }}</span>）
          </p>
        </div>
      </header>

      <el-alert
        v-if="forced"
        class="cp__alert"
        type="warning"
        :closable="false"
        show-icon
        title="首次登入或密碼已被重設，請先修改密碼"
      />

      <el-form
        label-position="top"
        @submit.prevent="submit"
      >
        <el-form-item
          label="目前密碼"
          :error="errors.current"
        >
          <el-input
            v-model="form.current"
            data-test="cp-current"
            type="password"
            show-password
            autocomplete="current-password"
            :disabled="loading"
          />
          <div
            v-if="forced && !errors.current"
            class="cp__field-hint"
          >
            請輸入管理員提供的臨時密碼
          </div>
        </el-form-item>
        <el-form-item
          label="新密碼"
          :error="errors.next"
        >
          <el-input
            v-model="form.next"
            data-test="cp-next"
            type="password"
            show-password
            autocomplete="new-password"
            :disabled="loading"
          />
          <ul
            class="pw-rules"
            aria-label="新密碼規則"
            aria-live="polite"
          >
            <li
              v-for="rule in rules"
              :key="rule.key"
              class="pw-rule"
              :class="`is-${rule.state}`"
              :data-state="rule.state"
            >
              <el-icon>
                <CircleCheckFilled v-if="rule.state === 'ok'" />
                <CircleCloseFilled v-else-if="rule.state === 'fail'" />
                <Remove v-else />
              </el-icon>
              <span>{{ rule.label }}</span>
              <span class="cp__sr-only">{{ RULE_SR_TEXT[rule.state] }}</span>
            </li>
          </ul>
        </el-form-item>
        <el-form-item
          label="確認新密碼"
          :error="errors.confirm"
        >
          <el-input
            v-model="form.confirm"
            data-test="cp-confirm"
            type="password"
            show-password
            autocomplete="new-password"
            :disabled="loading"
            @blur="onConfirmBlur"
          />
        </el-form-item>

        <div class="cp__foot">
          <el-button
            v-if="!forced"
            :icon="ArrowLeft"
            :disabled="loading"
            @click="goBack"
          >
            返回
          </el-button>
          <el-button
            v-else
            text
            :disabled="loading"
            @click="logout"
          >
            登出
          </el-button>
          <span class="cp__foot-spacer" />
          <el-button
            class="cp__submit"
            type="primary"
            native-type="submit"
            :loading="loading"
          >
            更新密碼
          </el-button>
        </div>
      </el-form>
    </main>
  </div>
</template>

<style scoped>
/* blank layout：撐滿整頁、灰底置中 */
.cp-page {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px 16px;
  background: var(--el-bg-color-page);
}

.cp {
  box-sizing: border-box;
  width: 100%;
  max-width: 440px;
  padding: 28px 28px 24px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  box-shadow: var(--el-box-shadow-lighter);
}

.cp__head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 20px;
}

.cp__icon {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  font-size: 20px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  border-radius: 10px;
}

.cp__title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  line-height: 28px;
  color: var(--el-text-color-primary);
}

.cp__account {
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.cp__mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.cp__alert {
  margin-bottom: 20px;
}

.cp .el-form-item {
  margin-bottom: 24px;
}

.cp__field-hint {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}

.pw-rules {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  padding: 0;
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 20px;
  list-style: none;
}

.pw-rule {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  color: var(--el-text-color-secondary);
}

.pw-rule.is-ok {
  color: var(--el-color-success);
}

.pw-rule.is-fail {
  color: var(--el-color-danger);
}

/* 規則狀態的報讀文字：不顯示，螢幕報讀器讀得到 */
.cp__sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

.cp__foot {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 8px;
}

.cp__foot-spacer {
  flex: 1;
}

.cp__foot .el-button + .el-button {
  margin-left: 0;
}

.cp__submit {
  min-width: 120px;
  height: 40px;
}

@media (max-width: 767px) {
  .cp {
    padding: 24px 20px 20px;
  }

  .cp__submit {
    flex: 1;
  }
}
</style>
