// FRONTEND-050：系統設定讀取與更新 api client（BACKEND-112、113）。
// 分組與 key 由後端 settings registry 驅動，前端不列舉；表單依 json_schema 產生。
import type { ISODateTime } from '@/shared/types/api'
import { adminHttp } from './http'

/** registry 驅動（目前為 org / pickup / homework / leave / notification / line） */
export type SettingGroup = string

/** Pydantic 產生的 JSON Schema（含 $defs / $ref），只描述前端會讀的欄位 */
export interface JsonSchema {
  type?: string | string[]
  title?: string
  description?: string
  properties?: Record<string, JsonSchema>
  required?: string[]
  additionalProperties?: boolean | JsonSchema
  items?: JsonSchema
  $ref?: string
  $defs?: Record<string, JsonSchema>
  anyOf?: JsonSchema[]
  enum?: unknown[]
  pattern?: string
  format?: string
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  default?: unknown
}

export interface Setting {
  key: string
  group: SettingGroup
  label: string
  is_secret: boolean
  /** secret 欄位已由後端遮罩 */
  value: Record<string, unknown>
  json_schema: JsonSchema
  updated_at: ISODateTime | null
  updated_by_name: string | null
}

export async function listSettings(): Promise<Setting[]> {
  const res = await adminHttp.get<{ items: Setting[] }>('/admin/settings')
  return res.data.items
}

/** 422 invalid_setting_value 的 details 為 Pydantic 錯誤清單，由 ApiError 原樣帶出 */
export async function updateSetting(key: string, value: Record<string, unknown>): Promise<Setting> {
  const res = await adminHttp.put<Setting>(`/admin/settings/${encodeURIComponent(key)}`, { value })
  return res.data
}
