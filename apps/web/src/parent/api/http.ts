// PARENT-004：家長端所有 api client 共用的 axios instance。
// 移植 ivy FE:src/parent/api/index.ts 的 refresh 去重語意（實作在 FRONTEND-001，這裡只做設定）；
// 去掉租戶三態、maintenance 503、staff session 偵測、離線佇列、timing buffer。
// 不 import router / store（避免循環）：未登入的處理由 PARENT-013 經 setUnauthenticatedHandler 注入。
import axios, { type AxiosInstance } from 'axios'
import { createHttpClient } from '@/shared/http/createHttpClient'
import { ApiError } from '@/shared/types/api'

/** 不掛攔截器的獨立 instance：refresh 不經 parentHttp 自身的 401 攔截器，避免遞迴 */
export const parentRefreshHttp: AxiosInstance = axios.create({ baseURL: '/api', withCredentials: true })

let unauthenticatedHandler: (() => void) | null = null

/**
 * parentRefreshHttp 不經 FRONTEND-001 正規化：帶 envelope 的錯誤轉成 ApiError，
 * FRONTEND-001 才認得 409 refresh_in_progress（另一分頁已輪替）並視同成功重送。
 */
function toApiError(error: unknown): unknown {
  const response = axios.isAxiosError(error) ? error.response : undefined
  const body = response?.data as { error?: { code?: unknown; message?: unknown; details?: unknown } } | undefined
  const e = body?.error
  if (!response || typeof e?.code !== 'string' || typeof e.message !== 'string') return error
  return new ApiError(response.status, e.code, e.message, e.details ?? null)
}

export const parentHttp: AxiosInstance = createHttpClient({
  baseURL: '/api',
  // refresh 端點必須列在這裡；綁定頁自己處理 bind 的 401
  noRefreshPaths: [
    '/parent/auth/liff-login',
    '/parent/auth/bind',
    '/parent/auth/refresh',
    '/parent/auth/logout',
    '/parent/config',
  ],
  refresh: () =>
    parentRefreshHttp.post('/parent/auth/refresh').then(
      () => undefined,
      (error: unknown) => {
        throw toApiError(error)
      },
    ),
  onAuthFailure: () => unauthenticatedHandler?.(),
})

export function setUnauthenticatedHandler(handler: () => void): void {
  unauthenticatedHandler = handler
}
