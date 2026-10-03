// FRONTEND-001：axios 工廠（後台 FRONTEND-020 與家長端各自建立 instance）。
// 移植 ivy FE:src/api/index.ts 的「401 → 單一 refresh promise → 重試一次」與 blob 錯誤回應解析回 JSON；
// 去掉 tenant、session generation、dedupe、Sentry、kill switch。
//
// 錯誤一律正規化為 ApiError 後 reject（取消除外，原樣 reject 供 isCanceled 判斷）。
// 401 流程：
//   - noRefreshPaths 的請求、refresh() 執行期間同步發出的請求（refresh 端點本身）→ 不 refresh、不呼叫 onAuthFailure。
//   - 其他請求第一次 401 → 共用同一個 refresh；成功（或 409 refresh_in_progress：另一個分頁已輪替）→ 重送一次；
//     refresh 失敗 → onAuthFailure 並 reject 原 401。
//   - 重送後仍 401 → onAuthFailure。onAuthFailure 以 refresh 那一輪為單位去重。
import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from 'axios'
import { ApiError, isApiError } from '@/shared/types/api'

export interface HttpClientOptions {
  /** 預設 '/api' */
  baseURL?: string
  /** 預設 30000 */
  timeoutMs?: number
  /** 呼叫對應的 refresh endpoint；reject = refresh 失敗 */
  refresh: () => Promise<void>
  /** 這些 url（相對 baseURL，前綴比對）的 401 不觸發 refresh，例如 ['/admin/auth/login', '/admin/auth/refresh'] */
  noRefreshPaths: string[]
  /** refresh 失敗，或重試後仍 401 */
  onAuthFailure: () => void
  /** 收到 403 password_change_required */
  onPasswordChangeRequired?: () => void
}

interface ClientRequestConfig extends InternalAxiosRequestConfig {
  _retried?: boolean
  /** 由 refresh() 發出的請求 */
  _fromRefresh?: boolean
  /** 重送前等待的那一輪 refresh，用來去重 onAuthFailure */
  _refreshRound?: Promise<void>
}

function isEnvelope(data: unknown): data is { error: { code: string; message: string; details?: unknown } } {
  if (data === null || typeof data !== 'object') return false
  const error = (data as { error?: unknown }).error
  if (error === null || typeof error !== 'object') return false
  const e = error as { code?: unknown; message?: unknown }
  return typeof e.code === 'string' && typeof e.message === 'string'
}

async function readBlobJson(data: unknown): Promise<unknown> {
  if (!(data instanceof Blob) || !data.type.includes('application/json')) return data
  try {
    return JSON.parse(await data.text())
  } catch {
    return data
  }
}

async function normalizeError(error: unknown): Promise<ApiError> {
  if (isApiError(error)) return error
  // 用 isAxiosError 而非 instanceof：axios 可能同時以 ESM / CJS 兩份載入（例如 axios-mock-adapter）
  const axiosError = axios.isAxiosError(error) ? error : null
  const response = axiosError?.response
  if (!response) {
    if (axiosError?.code === 'ECONNABORTED' || axiosError?.code === 'ETIMEDOUT') {
      return new ApiError(0, 'timeout', '連線逾時，請稍後再試')
    }
    return new ApiError(0, 'network_error', '網路連線異常，請稍後再試')
  }
  const data = await readBlobJson(response.data)
  if (isEnvelope(data)) {
    return new ApiError(response.status, data.error.code, data.error.message, data.error.details ?? null)
  }
  return new ApiError(response.status, `http_${response.status}`, `伺服器暫時無法回應（${response.status}）`)
}

export function createHttpClient(opts: HttpClientOptions): AxiosInstance {
  const instance = axios.create({
    baseURL: opts.baseURL ?? '/api',
    timeout: opts.timeoutMs ?? 30000,
    withCredentials: true,
    headers: { 'Content-Type': 'application/json' },
  })

  // 每個 instance 各自的 refresh 狀態（後台與家長端互不影響）
  let refreshing: Promise<void> | null = null
  let callingRefresh = false
  const notifiedRounds = new WeakSet<Promise<void>>()

  function isNoRefreshPath(url: string | undefined): boolean {
    return url !== undefined && opts.noRefreshPaths.some((p) => url.startsWith(p))
  }

  function startRefresh(): Promise<void> {
    if (refreshing) return refreshing
    let call: Promise<void>
    callingRefresh = true
    try {
      call = Promise.resolve(opts.refresh())
    } catch (e) {
      call = Promise.reject(e)
    } finally {
      callingRefresh = false
    }
    const round: Promise<void> = call.finally(() => {
      if (refreshing === round) refreshing = null
    })
    refreshing = round
    return round
  }

  function notifyAuthFailure(round: Promise<void> | undefined): void {
    if (round) {
      if (notifiedRounds.has(round)) return
      notifiedRounds.add(round)
    }
    opts.onAuthFailure()
  }

  // synchronous：在 instance.get() 呼叫當下執行，才能辨識 refresh() 同步發出的請求
  instance.interceptors.request.use(
    (config) => {
      ;(config as ClientRequestConfig)._fromRefresh = callingRefresh
      return config
    },
    undefined,
    { synchronous: true },
  )

  instance.interceptors.response.use(undefined, async (error: unknown) => {
    if (axios.isCancel(error)) throw error

    const apiError = await normalizeError(error)
    const config = (axios.isAxiosError(error) ? error.config : undefined) as ClientRequestConfig | undefined

    if (apiError.status === 401 && config && !config._fromRefresh && !isNoRefreshPath(config.url)) {
      if (config._retried) {
        notifyAuthFailure(config._refreshRound)
        throw apiError
      }
      config._retried = true
      const round = startRefresh()
      try {
        await round
      } catch (refreshError) {
        const inProgress = isApiError(refreshError) && refreshError.status === 409
        if (!inProgress) {
          notifyAuthFailure(round)
          throw apiError
        }
      }
      config._refreshRound = round
      return instance(config)
    }

    if (apiError.status === 403 && apiError.code === 'password_change_required') {
      opts.onPasswordChangeRequired?.()
    }
    throw apiError
  })

  return instance
}
