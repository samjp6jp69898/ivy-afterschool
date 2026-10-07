// FRONTEND-023：頁面與元件判斷權限的唯一入口（docs/architecture_decisions.md §6：頁內用 hasPermission()）。
// 移植 ivy FE:src/utils/auth.ts::hasPermission 的「無使用者一律 false」；去掉 teacher / portal 短路、
// scope 後綴與 `*` 萬用碼判斷（後端 /auth/me 已把 admin 的權限展開成全部權限碼）。
// 這裡只決定 UI 顯示（隱藏按鈕 / 唯讀）；安全邊界是後端的 require_permission，前端誤放行只會拿到 403。
import type { PermissionCode } from '@/constants/permissions'
import { useAuthStore } from '@/stores/auth'

type AuthStore = ReturnType<typeof useAuthStore>

function holds(auth: AuthStore, code: PermissionCode): boolean {
  return auth.user !== null && auth.permissions.has(code)
}

function holdsAny(auth: AuthStore, codes: readonly PermissionCode[]): boolean {
  return auth.user !== null && codes.some((code) => auth.permissions.has(code))
}

export function hasPermission(code: PermissionCode): boolean {
  return holds(useAuthStore(), code)
}

export function hasAnyPermission(codes: readonly PermissionCode[]): boolean {
  return holdsAny(useAuthStore(), codes)
}

export interface PermissionChecks {
  can: (code: PermissionCode) => boolean
  canAny: (codes: readonly PermissionCode[]) => boolean
}

/**
 * 在 setup 取得後於 template 直接呼叫 can / canAny。
 * 讀的是 auth store 的 reactive 狀態，渲染時被依賴追蹤，使用者或權限變更後自動重繪。
 */
export function usePermission(): PermissionChecks {
  const auth = useAuthStore()
  return {
    can: (code) => holds(auth, code),
    canAny: (codes) => holdsAny(auth, codes),
  }
}
