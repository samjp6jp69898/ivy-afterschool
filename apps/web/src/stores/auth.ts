// FRONTEND-022：員工登入狀態與權限集合。
// 移植 ivy FE:src/utils/auth.ts 的 setUserInfo / clearAuth / clearMustChangePassword 職責；
// 不做 storage 持久化：token 在 httpOnly cookie，重新載入時以 GET /admin/auth/me 還原。
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as authApi from '@/api/auth'
import type { StaffMe } from '@/api/auth'
import type { PermissionCode } from '@/constants/permissions'
import { isApiError } from '@/shared/types/api'

export type AuthStatus = 'unknown' | 'authenticated' | 'anonymous'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<StaffMe | null>(null)
  const status = ref<AuthStatus>('unknown')
  /** restore 遇到 401 以外的錯誤（網路）；登入頁顯示「無法連線伺服器」 */
  const restoreError = ref<unknown>(null)

  const permissions = computed<ReadonlySet<string>>(() => new Set(user.value?.permissions ?? []))
  const isAuthenticated = computed(() => status.value === 'authenticated')
  const mustChangePassword = computed(() => user.value?.must_change_password === true)

  let restoring: Promise<void> | null = null

  function setUser(next: StaffMe): void {
    user.value = next
    status.value = 'authenticated'
    restoreError.value = null
  }

  function reset(): void {
    user.value = null
    status.value = 'anonymous'
  }

  function restore(): Promise<void> {
    if (status.value !== 'unknown') return Promise.resolve()
    if (restoring) return restoring
    restoring = (async () => {
      try {
        setUser(await authApi.fetchMe())
      } catch (err) {
        // 401（refresh 也失敗）= 未登入；其他錯誤另外記下來給登入頁顯示
        restoreError.value = isApiError(err) && err.status === 401 ? null : err
        reset()
      } finally {
        restoring = null
      }
    })()
    return restoring
  }

  async function login(username: string, password: string): Promise<StaffMe> {
    const me = await authApi.login({ username, password })
    setUser(me)
    return me
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } catch {
      // 後端失敗也要清掉前端狀態
    }
    reset()
  }

  async function changePassword(current: string, next: string): Promise<StaffMe> {
    const me = await authApi.changePassword({ current_password: current, new_password: next })
    setUser(me)
    return me
  }

  function hasPermission(code: PermissionCode): boolean {
    return permissions.value.has(code)
  }

  function hasAnyPermission(codes: PermissionCode[]): boolean {
    return codes.some((c) => permissions.value.has(c))
  }

  return {
    user,
    status,
    restoreError,
    permissions,
    isAuthenticated,
    mustChangePassword,
    restore,
    login,
    logout,
    changePassword,
    setUser,
    reset,
    hasPermission,
    hasAnyPermission,
  }
})
