import type MockAdapter from 'axios-mock-adapter'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type RouteLocationNormalized } from 'vue-router'
import type { StaffMe } from '@/api/auth'
import { adminHttp, resetAdminHttpHandlers, setAdminHttpHandlers } from '@/api/http'
import { ALL_PERMISSION_CODES } from '@/constants/permissions'
import { useAuthStore } from '@/stores/auth'
import { createApiMock } from '@/test/helpers'
import { authGuard, firstAllowedPath, landingPath, safeRedirect } from './authGuard'
import { routes } from './routes'

// 只驗證守衛邏輯，不載入任何真實 view
vi.mock('@/router/viewLoaders', () => {
  const names = [
    'LoginView',
    'ChangePasswordView',
    'ErrorView',
    'DashboardView',
    'StudentWorkbenchView',
    'ClassesView',
    'AttendanceTodayView',
    'AttendanceMonthlyView',
    'LeavesView',
    'HomeworkBoardView',
    'PickupPosView',
    'PickupAuthorizationsView',
    'ExamsView',
    'ExamDetailView',
    'SystemSettingsView',
    'ReferenceDataView',
    'StaffAccountsView',
    'RolesView',
    'AuditLogView',
  ]
  return {
    VIEW_LOADERS: Object.fromEntries(
      names.map((name) => [name, () => Promise.resolve({ name: `Stub${name}`, render: () => null })]),
    ),
  }
})

/** docs/domain_spec.md §3 課輔老師（tutor）的預設權限 */
const TUTOR_PERMISSIONS = [
  'dashboard:read',
  'classes:read',
  'students:read',
  'attendance:read',
  'attendance:operate',
  'leaves:read',
  'homework:read',
  'homework:write',
  'pickup:read',
  'pickup:operate',
  'exams:read',
  'exams:write',
]

const router = createRouter({ history: createMemoryHistory(), routes })

/** 以真實路由表解析出守衛收到的 to（含 merged meta、fullPath、query）；resolve 結果只多了 href */
function to(fullPath: string): RouteLocationNormalized {
  return router.resolve(fullPath) as unknown as RouteLocationNormalized
}

/** 不經路由表的 to：模擬漏設 meta 的路由 */
function customTo(path: string, meta: Record<string, unknown>): RouteLocationNormalized {
  return {
    path,
    fullPath: path,
    name: undefined,
    hash: '',
    query: {},
    params: {},
    matched: [],
    redirectedFrom: undefined,
    meta,
  } as unknown as RouteLocationNormalized
}

function makeUser(permissions: readonly string[], overrides: Partial<StaffMe> = {}): StaffMe {
  return {
    id: 'u1',
    username: 'tutor01',
    display_name: '陳老師',
    role: { id: 'r4', code: 'tutor', name: '課輔老師' },
    permissions: [...permissions],
    must_change_password: false,
    ...overrides,
  }
}

function loginAs(permissions: readonly string[], overrides: Partial<StaffMe> = {}): void {
  useAuthStore().setUser(makeUser(permissions, overrides))
}

const perms = (...codes: string[]) => new Set(codes)

describe('authGuard', () => {
  let mock: MockAdapter

  beforeEach(() => {
    setActivePinia(createPinia())
    mock = createApiMock(adminHttp)
    setAdminHttpHandlers({ refresh: () => Promise.reject(new Error('refresh 失敗')), authFailure: () => undefined })
  })

  afterEach(() => {
    mock.restore()
    resetAdminHttpHandlers()
  })

  it('authGuard redirects anonymous users to login with redirect', async () => {
    mock.onGet('/admin/auth/me').reply(401, {
      error: { code: 'not_authenticated', message: '請先登入', details: null },
    })

    expect(await authGuard(to('/students?class_id=c1'))).toEqual({
      path: '/login',
      query: { redirect: '/students?class_id=c1' },
    })
    expect(await authGuard(to('/'))).toEqual({ path: '/login' })
    expect(await authGuard(to('/forbidden'))).toEqual({ path: '/login', query: { redirect: '/forbidden' } })
    expect(await authGuard(to('/change-password'))).toEqual({
      path: '/login',
      query: { redirect: '/change-password' },
    })
    // 還原只打一次 /auth/me，之後以 store 狀態判斷
    expect(mock.history.get).toHaveLength(1)
    expect(useAuthStore().status).toBe('anonymous')

    // public 頁面未登入可進：登入頁、404
    expect(await authGuard(to('/login'))).toBe(true)
    expect(await authGuard(to('/login?redirect=/students'))).toBe(true)
    expect(await authGuard(to('/no/such/page'))).toBe(true)
  })

  it('authGuard forces password change', async () => {
    loginAs(['students:read', 'dashboard:read'], { must_change_password: true })

    expect(await authGuard(to('/students'))).toBe('/change-password')
    expect(await authGuard(to('/'))).toBe('/change-password')
    expect(await authGuard(to('/forbidden'))).toBe('/change-password')
    expect(await authGuard(to('/change-password'))).toBe(true)
    // 已登入再進登入頁也直接去改密碼，不先繞到有權限的頁
    expect(await authGuard(to('/login'))).toBe('/change-password')
    expect(await authGuard(to('/login?redirect=/students'))).toBe('/change-password')
    expect(mock.history.get).toHaveLength(0)

    useAuthStore().setUser(makeUser(['students:read'], { must_change_password: false }))
    expect(await authGuard(to('/students'))).toBe(true)
  })

  it('authGuard denies routes without permission meta by default', async () => {
    loginAs(ALL_PERMISSION_CODES)

    expect(await authGuard(customTo('/x', { title: 'X' }))).toEqual({ path: '/forbidden', query: { from: '/x' } })
    expect(await authGuard(customTo('/x', {}))).toEqual({ path: '/forbidden', query: { from: '/x' } })
    // public / authOnly 以外的 falsy 旗標同樣視為漏設
    expect(await authGuard(customTo('/y', { title: 'Y', public: false, authOnly: false }))).toEqual({
      path: '/forbidden',
      query: { from: '/y' },
    })
    // 路由表每條帶 permission 的路由，全權限者都進得去（確認預設拒絕沒有誤傷正常路由）
    for (const r of routes) {
      if (r.meta?.permission && !r.path.includes(':')) expect(await authGuard(to(r.path)), r.path).toBe(true)
    }
  })

  it('authGuard sends missing permission to forbidden', async () => {
    loginAs(TUTOR_PERMISSIONS)

    expect(await authGuard(to('/settings/accounts'))).toEqual({
      path: '/forbidden',
      query: { from: '/settings/accounts' },
    })
    expect(await authGuard(to('/settings/roles?tab=director'))).toEqual({
      path: '/forbidden',
      query: { from: '/settings/roles?tab=director' },
    })
    expect(await authGuard(to('/settings/audit'))).toEqual({ path: '/forbidden', query: { from: '/settings/audit' } })
    // 被導到的 /forbidden 本身放行，不會再導一次
    expect(await authGuard(to('/forbidden?from=/settings/audit'))).toBe(true)
  })

  it('authGuard lands root on first allowed page', async () => {
    loginAs(['attendance:read', 'homework:read'])
    expect(await authGuard(to('/'))).toBe('/attendance')

    loginAs([])
    expect(await authGuard(to('/'))).toBe('/forbidden')
    expect(await authGuard(to('/forbidden'))).toBe(true)

    loginAs(['dashboard:read', 'students:read'])
    expect(await authGuard(to('/'))).toBe(true)

    // firstAllowedPath 依路由表順序、跳過帶參數的路徑
    expect(firstAllowedPath(perms('attendance:read', 'homework:read'))).toBe('/attendance')
    expect(firstAllowedPath(perms('exams:read'))).toBe('/exams')
    expect(firstAllowedPath(perms('dashboard:read', 'students:read'))).toBe('/')
    expect(firstAllowedPath(perms('audit:read'))).toBe('/settings/audit')
    expect(firstAllowedPath(perms())).toBeNull()
    expect(firstAllowedPath(perms('no:such'))).toBeNull()
  })

  it('authGuard redirects logged-in user away from login', async () => {
    // firstAllowedPath 是 /students；redirect 目標選 /attendance 以便和 fallback 區分
    loginAs(['attendance:read', 'students:read'])

    expect(await authGuard(to('/login?redirect=/attendance'))).toBe('/attendance')
    expect(await authGuard(to('/login?redirect=/attendance?date=2026-10-07'))).toBe('/attendance?date=2026-10-07')
    expect(await authGuard(to('/login?redirect=/attendance/monthly'))).toBe('/attendance/monthly')
    expect(await authGuard(to('/login'))).toBe('/students')
    expect(await authGuard(to('/login?redirect=//evil.example.com'))).toBe('/students')
    expect(await authGuard(to('/login?redirect=https://evil.example.com/students'))).toBe('/students')
    // 無該頁權限的 redirect 不採用
    expect(await authGuard(to('/login?redirect=/settings/roles'))).toBe('/students')
    expect(await authGuard(to('/login?redirect=/exams/e1'))).toBe('/students')
    // public / authOnly 的目標不算有權限的頁：不會導回登入頁形成迴圈，也不會落在錯誤頁
    expect(await authGuard(to('/login?redirect=/login'))).toBe('/students')
    expect(await authGuard(to('/login?redirect=/forbidden'))).toBe('/students')
    expect(await authGuard(to('/login?redirect=/no/such/page'))).toBe('/students')
    // 多個 redirect（陣列）不採用
    expect(await authGuard(to('/login?redirect=/attendance&redirect=/students'))).toBe('/students')

    loginAs([])
    expect(await authGuard(to('/login?redirect=/attendance'))).toBe('/forbidden')
    expect(await authGuard(to('/login'))).toBe('/forbidden')

    // landingPath 同一套規則，供登入頁登入成功後使用
    const p = perms('attendance:read', 'students:read')
    expect(landingPath('/attendance', p)).toBe('/attendance')
    expect(landingPath('/settings/roles', p)).toBe('/students')
    expect(landingPath(undefined, p)).toBe('/students')
    expect(landingPath('//evil.example.com', p)).toBe('/students')
    expect(landingPath('/attendance', perms())).toBe('/forbidden')
  })

  it('authGuard safeRedirect rejects external urls', () => {
    expect(safeRedirect('/exams/1')).toBe('/exams/1')
    expect(safeRedirect('/students?class_id=c1&page=2')).toBe('/students?class_id=c1&page=2')
    expect(safeRedirect('/')).toBe('/')
    expect(safeRedirect('//evil.com')).toBeNull()
    expect(safeRedirect('/\\evil.com')).toBeNull()
    expect(safeRedirect('https://evil.com')).toBeNull()
    expect(safeRedirect('http://evil.com/students')).toBeNull()
    expect(safeRedirect('/students?next=https://evil.com')).toBeNull()
    expect(safeRedirect('javascript:alert(1)')).toBeNull()
    expect(safeRedirect('students')).toBeNull()
    expect(safeRedirect('')).toBeNull()
    expect(safeRedirect(['/a'])).toBeNull()
    expect(safeRedirect(null)).toBeNull()
    expect(safeRedirect(undefined)).toBeNull()
    expect(safeRedirect(42)).toBeNull()
  })

  it('authGuard allows permitted routes', async () => {
    loginAs(['pickup:read'])

    expect(await authGuard(to('/pickup/authorizations'))).toBe(true)
    expect(await authGuard(to('/pickup'))).toBe(true)
    expect(await authGuard(to('/forbidden'))).toBe(true)
    expect(await authGuard(to('/change-password'))).toBe(true)
    expect(await authGuard(to('/students'))).toEqual({ path: '/forbidden', query: { from: '/students' } })

    loginAs(['exams:read'])
    expect(await authGuard(to('/exams/e1?tab=summary'))).toBe(true)
    expect(await authGuard(to('/exams'))).toBe(true)
    expect(mock.history.get).toHaveLength(0)
  })

  it('authGuard redirect targets are stable and never loop', async () => {
    // 每個導向結果再過一次守衛都放行（守衛不會把人踢來踢去）
    const settle = async (start: RouteLocationNormalized) => {
      const first = await authGuard(start)
      if (first === true) return { hops: 0, path: start.fullPath }
      const next = to(router.resolve(first).fullPath)
      expect(await authGuard(next), `${start.fullPath} → ${next.fullPath}`).toBe(true)
      return { hops: 1, path: next.fullPath }
    }

    mock.onGet('/admin/auth/me').reply(401, {
      error: { code: 'not_authenticated', message: '請先登入', details: null },
    })
    expect(await settle(to('/students'))).toEqual({ hops: 1, path: '/login?redirect=/students' })

    loginAs(['students:read'], { must_change_password: true })
    expect(await settle(to('/students'))).toEqual({ hops: 1, path: '/change-password' })
    expect(await settle(to('/login'))).toEqual({ hops: 1, path: '/change-password' })

    loginAs(TUTOR_PERMISSIONS)
    expect(await settle(to('/settings/roles'))).toEqual({ hops: 1, path: '/forbidden?from=/settings/roles' })
    expect(await settle(to('/login?redirect=/settings/roles'))).toEqual({ hops: 1, path: '/' })
    expect(await settle(to('/login?redirect=/login'))).toEqual({ hops: 1, path: '/' })

    loginAs([])
    expect(await settle(to('/'))).toEqual({ hops: 1, path: '/forbidden' })
    expect(await settle(to('/login'))).toEqual({ hops: 1, path: '/forbidden' })
  })
})
