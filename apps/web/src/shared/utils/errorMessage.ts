// FRONTEND-009：錯誤轉使用者可讀文字的唯一入口（前後台共用）。
// 移植 ivy FE:src/utils/apiErrorMessage.ts::apiErrorMessage 的「有後端訊息用後端、否則 fallback」原則；
// 只信任 ApiError（FRONTEND-001 正規化後的錯誤），其他錯誤的 message 可能是英文技術訊息，不直接顯示。
import axios from 'axios'
import { isApiError, type ValidationErrorItem } from '@/shared/types/api'

/** 請求被取消（換頁、重複查詢）：呼叫端應靜默忽略 */
export function isCanceled(err: unknown): boolean {
  return axios.isCancel(err)
}

export function errorMessage(err: unknown, fallback: string): string {
  if (isApiError(err) && err.message) return err.message
  return fallback
}

export function errorCode(err: unknown): string | null {
  return isApiError(err) ? err.code : null
}

const LOC_PREFIXES = new Set<string | number>(['body', 'query'])

function isValidationItem(v: unknown): v is ValidationErrorItem {
  if (v === null || typeof v !== 'object') return false
  const item = v as Partial<ValidationErrorItem>
  return Array.isArray(item.loc) && typeof item.msg === 'string'
}

/**
 * 422 validation_error 的 details → `{ 欄位: 訊息 }`，供表單顯示在對應 el-form-item。
 * 欄位名為 loc 去掉開頭 'body' / 'query' 後以 '.' 串接；同一欄位只留第一則；非 validation_error 回 {}。
 */
export function validationFieldErrors(err: unknown): Record<string, string> {
  if (!isApiError(err) || err.code !== 'validation_error' || !Array.isArray(err.details)) return {}
  const out: Record<string, string> = {}
  for (const item of err.details as unknown[]) {
    if (!isValidationItem(item)) continue
    const loc = item.loc.length > 0 && LOC_PREFIXES.has(item.loc[0]!) ? item.loc.slice(1) : item.loc
    const field = loc.join('.')
    if (field && !(field in out)) out[field] = item.msg
  }
  return out
}
