import type MockAdapter from 'axios-mock-adapter'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createMemoryHistory,
  isNavigationFailure,
  NavigationFailureType,
  type RouteMeta,
  type Router,
} from 'vue-router'
import type { StaffMe } from '@/api/auth'
import { adminHttp, resetAdminHttpHandlers, setAdminHttpHandlers } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { createApiMock } from '@/test/helpers'
import { APP_TITLE, createAdminRouter } from './index'

// 只驗證 router 的接線，不載入任何真實 view
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

const UNAUTHENTICATED = { error: { code: 'not_authenticated', message: '請先登入', details: null } }

function makeUser(permissions: string[], overrides: Partial<StaffMe> = {}): StaffMe {
  return {
    id: 'u1',
    username: 'clerk01',
    display_name: '林行政',
    role: { id: 'r1', code: 'clerk', name: '行政' },
    permissions,
    must_change_password: false,
    ...overrides,
  }
}

function loginAs(permissions: string[], overrides: Partial<StaffMe> = {}): void {
  useAuthStore().setUser(makeUser(permissions, overrides))
}

describe('admin router', () => {
  let mock: MockAdapter
  const routers: Router[] = []

  function memoryRouter(): Router {
    const router = createAdminRouter(createMemoryHistory())
    routers.push(router)
    return router
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    mock = createApiMock(adminHttp)
    setAdminHttpHandlers({ refresh: () => Promise.reject(new Error('refresh 失敗')), authFailure: () => undefined })
    document.title = ''
  })

  afterEach(() => {
    for (const r of routers.splice(0)) r.options.history.destroy()
    mock.restore()
    resetAdminHttpHandlers()
  })

  it('admin router redirects anonymous navigation to login', async () => {
    mock.onGet('/admin/auth/me').reply(401, UNAUTHENTICATED)
    const router = memoryRouter()

    await router.push('/students')

    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.redirect).toBe('/students')
    expect(router.currentRoute.value.name).toBe('login')
    expect(document.title).toBe(`登入 - ${APP_TITLE}`)
    expect(mock.history.get).toHaveLength(1)

    // 帶 query 的目標：redirect 保留完整 fullPath，之後導回時原樣還原
    await router.push('/students?class_id=c1')
    expect(router.currentRoute.value.query.redirect).toBe('/students?class_id=c1')

    // 首頁不帶 redirect
    await router.push('/')
    expect(router.currentRoute.value.fullPath).toBe('/login')
  })

  it('admin router lets a logged-in user through and back from login', async () => {
    loginAs(['students:read', 'attendance:read'])
    const router = memoryRouter()

    await router.push('/students?class_id=c1')
    expect(router.currentRoute.value.fullPath).toBe('/students?class_id=c1')
    expect(router.currentRoute.value.name).toBe('students')

    // 已登入再進登入頁：依 redirect 導回（含 query），沒權限的 redirect 落到第一個有權限的頁
    await router.push('/login?redirect=/attendance?date=2026-10-07')
    expect(router.currentRoute.value.fullPath).toBe('/attendance?date=2026-10-07')
    await router.push({ path: '/login', query: { redirect: '/settings/roles' } })
    expect(router.currentRoute.value.fullPath).toBe('/students')
    expect(mock.history.get).toHaveLength(0)
  })

  it('admin router updates document title', async () => {
    loginAs(['students:read'])
    const router = memoryRouter()

    await router.push('/students')
    expect(document.title).toBe(`學生工作台 - ${APP_TITLE}`)

    // 被導向時標題是落點的
    await router.push('/settings/roles')
    expect(router.currentRoute.value.path).toBe('/forbidden')
    expect(router.currentRoute.value.query.from).toBe('/settings/roles')
    expect(document.title).toBe(`沒有權限 - ${APP_TITLE}`)

    await router.push('/change-password')
    expect(document.title).toBe(`修改密碼 - ${APP_TITLE}`)
  })

  it('admin router resolves unknown path to not found', async () => {
    loginAs(['students:read'])
    const router = memoryRouter()

    await router.push('/no-such-page')

    expect(router.currentRoute.value.name).toBe('not-found')
    expect(router.currentRoute.value.path).toBe('/no-such-page')
    expect(document.title).toBe(`找不到頁面 - ${APP_TITLE}`)

    // 404 是 public 頁：未登入也直接顯示，不導去登入
    useAuthStore().reset()
    const anonymous = memoryRouter()
    await anonymous.push('/also/missing')
    expect(anonymous.currentRoute.value.name).toBe('not-found')
    expect(mock.history.get).toHaveLength(0)
  })

  it('admin router forces password change on every navigation', async () => {
    loginAs(['students:read'], { must_change_password: true })
    const router = memoryRouter()

    await router.push('/students')
    expect(router.currentRoute.value.path).toBe('/change-password')

    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/change-password')

    useAuthStore().setUser(makeUser(['students:read'], { must_change_password: false }))
    await router.push('/students')
    expect(router.currentRoute.value.path).toBe('/students')
  })

  it('admin router keeps title on failed navigation', async () => {
    loginAs(['students:read', 'classes:read'])
    const router = memoryRouter()
    await router.push('/students')
    expect(document.title).toBe(`學生工作台 - ${APP_TITLE}`)

    // 重複導航（NAVIGATION_DUPLICATED）：畫面沒換，標題也不換
    document.title = '哨兵標題'
    const duplicated = await router.push('/students')
    expect(isNavigationFailure(duplicated, NavigationFailureType.duplicated)).toBe(true)
    expect(document.title).toBe('哨兵標題')

    // 被後掛的 guard 中止（NAVIGATION_ABORTED）：仍停在 /students，標題不換
    router.beforeEach((to) => to.path !== '/classes')
    const aborted = await router.push('/classes')
    expect(isNavigationFailure(aborted, NavigationFailureType.aborted)).toBe(true)
    expect(router.currentRoute.value.path).toBe('/students')
    expect(document.title).toBe('哨兵標題')

    // 正常導航恢復更新
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/students')
    await router.push('/change-password')
    expect(document.title).toBe(`修改密碼 - ${APP_TITLE}`)
  })

  it('admin router falls back to app title without meta.title', async () => {
    loginAs([])
    const router = memoryRouter()
    router.addRoute({
      path: '/untitled',
      name: 'untitled',
      component: { render: () => null },
      // 路由表的 RouteMeta 要求 title；這裡刻意模擬漏設
      meta: { authOnly: true } as unknown as RouteMeta,
    })

    await router.push('/untitled')

    expect(router.currentRoute.value.name).toBe('untitled')
    expect(document.title).toBe(APP_TITLE)
    expect(document.title).not.toContain('undefined')
  })

  it('admin router uses hash history by default', () => {
    const router = createAdminRouter()
    routers.push(router)

    expect(router.options.history.base.endsWith('#')).toBe(true)
    expect(router.getRoutes().map((r) => r.name)).toContain('students')
  })
})
