import { describe, expect, it, vi } from 'vitest'
import type { Component } from 'vue'
import type { RouteRecordRaw } from 'vue-router'
import { isPermissionCode } from '@/constants/permissions'
import { mountWithApp } from '@/test/helpers'
import { VIEW_LOADERS } from '@/router/viewLoaders'
import { ROUTE_PERMISSIONS, routes } from './routes'
import routesSource from './routes.ts?raw'

// 只驗證路由表與 meta，不載入任何真實 view
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

function flatten(records: RouteRecordRaw[]): RouteRecordRaw[] {
  return records.flatMap((r) => [r, ...flatten(r.children ?? [])])
}

const allRoutes = flatten(routes)

describe('admin routes', () => {
  it('admin routes every route has exactly one access mode', () => {
    expect(allRoutes.length).toBe(20)
    for (const r of allRoutes) {
      const modes = [r.meta?.public === true, r.meta?.authOnly === true, r.meta?.permission != null]
      expect(modes.filter(Boolean), `${r.path} 的存取模式`).toHaveLength(1)
      expect(typeof r.meta?.title, `${r.path} 的 title`).toBe('string')
    }
  })

  it('admin routes permission table matches domain_spec', () => {
    expect(ROUTE_PERMISSIONS).toEqual({
      '/': 'dashboard:read',
      '/students': 'students:read',
      '/classes': 'classes:read',
      '/attendance': 'attendance:read',
      '/attendance/monthly': 'attendance:read',
      '/leaves': 'leaves:read',
      '/homework': 'homework:read',
      '/pickup': 'pickup:read',
      '/pickup/authorizations': 'pickup:read',
      '/exams': 'exams:read',
      '/exams/:id': 'exams:read',
      '/settings/system': 'settings:read',
      '/settings/reference': 'settings:read',
      '/settings/accounts': 'staff:read',
      '/settings/roles': 'roles:read',
      '/settings/audit': 'audit:read',
    })
    // ROUTE_PERMISSIONS 與路由表本身一致
    for (const r of allRoutes) {
      if (r.meta?.permission) expect(ROUTE_PERMISSIONS[r.path]).toBe(r.meta.permission)
    }
  })

  it('admin routes permissions are valid codes and names unique', () => {
    for (const r of allRoutes) {
      if (r.meta?.permission != null) expect(isPermissionCode(r.meta.permission)).toBe(true)
    }
    const names = allRoutes.map((r) => r.name)
    expect(names.every((n) => typeof n === 'string')).toBe(true)
    expect(new Set(names).size).toBe(names.length)

    const byPath = Object.fromEntries(allRoutes.map((r) => [r.path, r]))
    for (const p of ['/attendance', '/homework', '/pickup', '/pickup/authorizations']) {
      expect(byPath[p]?.meta?.tablet, p).toBe(true)
    }
    expect(allRoutes.filter((r) => r.meta?.tablet).map((r) => r.path).sort()).toEqual(
      ['/attendance', '/homework', '/pickup', '/pickup/authorizations'].sort(),
    )
  })

  it('admin routes special pages meta and props', () => {
    const byPath = Object.fromEntries(allRoutes.map((r) => [r.path, r]))

    expect(byPath['/login']?.meta).toMatchObject({ public: true, layout: 'blank' })
    expect(byPath['/change-password']?.meta).toMatchObject({
      authOnly: true,
      allowWhenMustChangePassword: true,
      layout: 'blank',
    })
    expect(byPath['/forbidden']?.meta).toMatchObject({ authOnly: true })
    expect(byPath['/forbidden']?.props).toEqual({ kind: 'forbidden' })
    expect(byPath['/:pathMatch(.*)*']?.name).toBe('not-found')
    expect(byPath['/:pathMatch(.*)*']?.meta).toMatchObject({ public: true, layout: 'blank' })
    expect(byPath['/:pathMatch(.*)*']?.props).toEqual({ kind: 'not_found' })
    expect(byPath['/exams/:id']?.props).toBe(true)
    expect(byPath['/students']?.meta?.title).toBe('學生工作台')
    // 只有 blank 版面的頁面設 layout，其餘預設 admin
    expect(allRoutes.filter((r) => r.meta?.layout === 'blank').map((r) => r.path).sort()).toEqual(
      ['/:pathMatch(.*)*', '/change-password', '/login'].sort(),
    )
    // catch-all 必須在最後
    expect(routes.at(-1)?.path).toBe('/:pathMatch(.*)*')
  })

  it('admin routes lazy-load every view through VIEW_LOADERS only', () => {
    const loaders = new Set<unknown>(Object.values(VIEW_LOADERS))
    for (const r of allRoutes) {
      expect(loaders.has((r as { component?: unknown }).component), `${r.path} 的 component`).toBe(true)
    }
    expect(routesSource).not.toMatch(/import\s*\(/)
    expect(routesSource).not.toMatch(/from\s+['"]@\/views/)
  })

  it('admin routes use placeholder loaders by default', async () => {
    const actual = await vi.importActual<typeof import('@/router/viewLoaders')>('@/router/viewLoaders')
    const loaded = (await actual.VIEW_LOADERS.StudentWorkbenchView()) as Component | { default: Component }
    const component = 'default' in loaded ? loaded.default : loaded

    const { wrapper } = await mountWithApp(component, {
      routes: [{ path: '/students', component, meta: { title: '學生工作台' } }],
      initialRoute: '/students',
    })

    expect(wrapper.text()).toContain('此頁面尚未實作：學生工作台')
  })
})
