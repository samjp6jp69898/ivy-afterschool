// FRONTEND-020：後台所有 api client 共用的 axios instance。
// refresh 與失敗處理由啟動流程（FRONTEND-028）注入，避免 http ↔ store ↔ router 循環 import；
// 每次呼叫時才讀取目前註冊的 handler，不在建立時綁死。
import type { AxiosInstance } from 'axios'
import { createHttpClient } from '@/shared/http/createHttpClient'

export interface AdminHttpHandlers {
  /** 預設：reject（尚未注入時一律視為 refresh 失敗） */
  refresh: () => Promise<void>
  /** 預設：location.hash = '#/login' */
  authFailure: () => void
  /** 預設：location.hash = '#/change-password' */
  passwordChangeRequired: () => void
}

const DEFAULT_HANDLERS: AdminHttpHandlers = {
  refresh: () => Promise.reject(new Error('refresh handler 尚未注入')),
  authFailure: () => {
    location.hash = '#/login'
  },
  passwordChangeRequired: () => {
    location.hash = '#/change-password'
  },
}

let handlers: AdminHttpHandlers = { ...DEFAULT_HANDLERS }

export const adminHttp: AxiosInstance = createHttpClient({
  baseURL: '/api',
  // refresh 端點必須列在這裡：它自己的 401 不可再觸發 refresh（FRONTEND-001 的主要防線）；
  // 改密碼的 401 代表舊密碼流程問題，不應 refresh
  noRefreshPaths: [
    '/admin/auth/login',
    '/admin/auth/refresh',
    '/admin/auth/logout',
    '/admin/auth/change-password',
  ],
  refresh: () => handlers.refresh(),
  onAuthFailure: () => handlers.authFailure(),
  onPasswordChangeRequired: () => handlers.passwordChangeRequired(),
})

export function setAdminHttpHandlers(h: Partial<AdminHttpHandlers>): void {
  handlers = { ...handlers, ...h }
}

/** http 以外的認證失敗（例如 ws 以 4401 / 4403 關閉）也走同一個 handler 導回登入 */
export function triggerAuthFailure(): void {
  handlers.authFailure()
}

/** 測試用：回到預設 handler */
export function resetAdminHttpHandlers(): void {
  handlers = { ...DEFAULT_HANDLERS }
}
