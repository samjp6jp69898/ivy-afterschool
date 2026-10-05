// PARENT-005：家長端公開設定（BACKEND-125，不需登入）。
// 同一頁面生命週期只打一次：in-flight 與成功結果共用同一個 promise；失敗時清除快取讓下次呼叫重試。
import { parentHttp } from './http'

export interface ParentLimits {
  leave_past_days: number
  leave_future_days: number
  leave_max_attachments: number
  leave_max_attachment_mb: number
  authorization_max_days_ahead: number
  persons_max: number
}

export interface PublicConfig {
  liff_id: string
  org_name: string
  org_phone: string | null
  logo_url: string | null
  add_friend_url: string | null
  limits: ParentLimits
}

export const DEFAULT_PARENT_LIMITS: ParentLimits = {
  leave_past_days: 30,
  leave_future_days: 60,
  leave_max_attachments: 3,
  leave_max_attachment_mb: 10,
  authorization_max_days_ahead: 14,
  persons_max: 10,
}

let cached: Promise<PublicConfig> | null = null

export function getPublicConfig(): Promise<PublicConfig> {
  if (cached) return cached
  const request = parentHttp.get<PublicConfig>('/parent/config').then(
    (res) => res.data,
    (error: unknown) => {
      if (cached === request) cached = null
      throw error
    },
  )
  cached = request
  return request
}

/** config 取得失敗時回 DEFAULT_PARENT_LIMITS（後端仍會驗證，前端上限只用於提示） */
export function getParentLimits(): Promise<ParentLimits> {
  return getPublicConfig().then(
    (config) => config.limits,
    () => DEFAULT_PARENT_LIMITS,
  )
}

export function _resetPublicConfigForTests(): void {
  cached = null
}
