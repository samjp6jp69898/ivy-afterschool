<script setup lang="ts">
// FRONTEND-047：員工登入頁。移植 ivy FE:src/views/LoginView.vue 的表單驗證、第一個錯誤欄位聚焦、送出中 loading；
// 去掉 tenant branding、portal-only 導向、閒置登出提示、品牌插圖與「教職員入口」。
// 設計稿：docs/mockups/page-login.html。路由為 blank layout，本元件自己撐滿整頁並置中。
// 登入後落點一律交給 landingPath（FRONTEND-025），不在這裡重做 redirect 檢查。
import { Lock, School, User } from '@element-plus/icons-vue'
import type { InputInstance } from 'element-plus'
import { nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchPublicConfig } from '@/api/auth'
import { landingPath } from '@/router/authGuard'
import { errorCode, errorMessage } from '@/shared/utils/errorMessage'
import { useAuthStore } from '@/stores/auth'

const DEFAULT_ORG_NAME = '安親班管理系統'
const NETWORK_MESSAGE = '無法連線伺服器，請稍後再試'
const NETWORK_CODES = new Set(['network_error', 'timeout'])

type Field = 'username' | 'password'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const configLoading = ref(true)
const orgName = ref('')
const logoUrl = ref<string | null>(null)
const logoBroken = ref(false)

const form = reactive({ username: '', password: '' })
const errors = reactive<Record<Field, string>>({ username: '', password: '' })
const alert = ref<{ type: 'error' | 'warning'; message: string } | null>(
  auth.restoreError ? { type: 'warning', message: NETWORK_MESSAGE } : null,
)
const loading = ref(false)
const usernameRef = ref<InputInstance | null>(null)
const passwordRef = ref<InputInstance | null>(null)

async function loadConfig(): Promise<void> {
  try {
    const config = await fetchPublicConfig()
    orgName.value = config.org_name.trim() || DEFAULT_ORG_NAME
    logoUrl.value = config.logo_url
  } catch {
    // 讀不到設定不影響登入，也不顯示錯誤
    orgName.value = DEFAULT_ORG_NAME
  } finally {
    configLoading.value = false
  }
}

onMounted(() => {
  void loadConfig()
  void nextTick(() => usernameRef.value?.focus())
})

function clearError(field: Field): void {
  errors[field] = ''
}

/** 只在送出時驗證；游標移到第一個錯誤欄位 */
function validate(): boolean {
  errors.username = form.username.trim() ? '' : '請輸入帳號'
  errors.password = form.password ? '' : '請輸入密碼'
  if (errors.username) usernameRef.value?.focus()
  else if (errors.password) passwordRef.value?.focus()
  return !errors.username && !errors.password
}

async function submit(): Promise<void> {
  if (loading.value || !validate()) return
  alert.value = null
  loading.value = true
  let focusPassword = false
  try {
    const me = await auth.login(form.username.trim(), form.password)
    const target = me.must_change_password ? '/change-password' : landingPath(route.query.redirect, auth.permissions)
    await router.replace(target)
  } catch (err) {
    const code = errorCode(err)
    if (code === 'invalid_credentials') {
      alert.value = { type: 'error', message: '帳號或密碼錯誤' }
      form.password = ''
      focusPassword = true
    } else if (code !== null && NETWORK_CODES.has(code)) {
      alert.value = { type: 'warning', message: NETWORK_MESSAGE }
    } else {
      // too_many_attempts 等其他錯誤顯示後端訊息
      alert.value = { type: 'error', message: errorMessage(err, '登入失敗，請稍後再試') }
    }
  } finally {
    loading.value = false
  }
  if (focusPassword) {
    // 送出中欄位是 disabled，等解除後才能聚焦
    await nextTick()
    passwordRef.value?.focus()
  }
}
</script>

<template>
  <div class="login-page">
    <main
      class="login"
      aria-labelledby="login-title"
    >
      <header class="login__brand">
        <div
          v-if="configLoading"
          class="login__logo"
          aria-hidden="true"
        />
        <img
          v-else-if="logoUrl && !logoBroken"
          class="login__logo"
          :src="logoUrl"
          :alt="`${orgName} Logo`"
          @error="logoBroken = true"
        >
        <div
          v-else
          class="login__logo is-fallback"
          aria-hidden="true"
        >
          <el-icon><School /></el-icon>
        </div>
        <el-skeleton
          v-if="configLoading"
          animated
          class="login__org-skeleton"
        >
          <template #template>
            <el-skeleton-item
              variant="h3"
              class="login__org-skeleton-item"
            />
          </template>
        </el-skeleton>
        <h1
          v-else
          id="login-title"
          class="login__org"
        >
          {{ orgName }}
        </h1>
        <p class="login__sub">
          員工登入
        </p>
      </header>

      <section class="login__card">
        <el-alert
          v-if="alert"
          class="login__alert"
          :type="alert.type"
          :title="alert.message"
          :closable="false"
          show-icon
          role="alert"
        />
        <el-form
          label-position="top"
          size="large"
          @submit.prevent="submit"
        >
          <el-form-item
            label="帳號"
            :error="errors.username"
          >
            <el-input
              ref="usernameRef"
              v-model="form.username"
              autocomplete="username"
              placeholder="請輸入帳號"
              :disabled="loading"
              :prefix-icon="User"
              @input="clearError('username')"
            />
          </el-form-item>
          <el-form-item
            label="密碼"
            :error="errors.password"
          >
            <el-input
              ref="passwordRef"
              v-model="form.password"
              type="password"
              show-password
              autocomplete="current-password"
              placeholder="請輸入密碼"
              :disabled="loading"
              :prefix-icon="Lock"
              @input="clearError('password')"
            />
          </el-form-item>
          <el-button
            class="login__submit"
            type="primary"
            native-type="submit"
            :loading="loading"
          >
            {{ loading ? '登入中…' : '登入' }}
          </el-button>
        </el-form>
        <p class="login__help">
          忘記密碼時，請聯絡主任或系統管理員重設
        </p>
      </section>
    </main>
  </div>
</template>

<style scoped>
/* blank layout：撐滿整頁、灰底置中 */
.login-page {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px 16px;
  background: var(--el-bg-color-page);
}

.login {
  width: 100%;
  max-width: 400px;
}

.login__brand {
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: center;
  margin-bottom: 24px;
  text-align: center;
}

.login__logo {
  box-sizing: border-box;
  flex-shrink: 0;
  width: 64px;
  height: 64px;
  object-fit: contain;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 16px;
}

.login__logo.is-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 32px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-8);
}

.login__org {
  min-height: 30px;
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  line-height: 30px;
  color: var(--el-text-color-primary);
  word-break: break-word;
}

/* 保留名稱高度，避免載入後版面跳動 */
.login__org-skeleton {
  width: 160px;
  height: 30px;
}

.login__org-skeleton-item {
  width: 160px;
  height: 26px;
}

.login__sub {
  margin: -4px 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.login__card {
  box-sizing: border-box;
  padding: 28px 28px 24px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  box-shadow: var(--el-box-shadow-lighter);
}

.login__alert {
  margin-bottom: 18px;
}

.login__card .el-form-item {
  margin-bottom: 22px;
}

.login__submit {
  width: 100%;
  height: 44px;
  font-size: 15px;
}

.login__help {
  margin: 16px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
  text-align: center;
}

@media (max-width: 767px) {
  .login__card {
    padding: 24px 20px 20px;
  }

  .login__org {
    font-size: 20px;
  }
}
</style>
