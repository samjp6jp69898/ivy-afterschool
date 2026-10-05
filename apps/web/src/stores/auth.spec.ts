import type MockAdapter from 'axios-mock-adapter'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { StaffMe } from '@/api/auth'
import { adminHttp, resetAdminHttpHandlers, setAdminHttpHandlers } from '@/api/http'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { useAuthStore } from './auth'

function makeUser(overrides: Partial<StaffMe> = {}): StaffMe {
  return {
    id: 'u1',
    username: 'clerk01',
    display_name: '林行政',
    role: { id: 'r1', code: 'clerk', name: '行政' },
    permissions: ['attendance:operate', 'students:read'],
    must_change_password: false,
    ...overrides,
  }
}

describe('auth store', () => {
  let mock: MockAdapter

  beforeEach(() => {
    setActivePinia(createPinia())
    mock = createApiMock(adminHttp)
    // refresh 失敗：401 直接變成認證失敗，不導頁
    setAdminHttpHandlers({ refresh: () => Promise.reject(new Error('refresh 失敗')), authFailure: () => undefined })
  })

  afterEach(() => {
    mock.restore()
    resetAdminHttpHandlers()
  })

  it('auth store restore sets authenticated user', async () => {
    mock.onGet('/admin/auth/me').reply(200, makeUser())
    const store = useAuthStore()
    expect(store.status).toBe('unknown')

    await store.restore()

    expect(store.status).toBe('authenticated')
    expect(store.isAuthenticated).toBe(true)
    expect(store.user?.display_name).toBe('林行政')
    expect(store.hasPermission('students:read')).toBe(true)
    expect(store.hasPermission('staff:write')).toBe(false)
    expect([...store.permissions]).toEqual(['attendance:operate', 'students:read'])
  })

  it('auth store restore becomes anonymous on 401', async () => {
    mock.onGet('/admin/auth/me').reply(401, {
      error: { code: 'not_authenticated', message: '請先登入', details: null },
    })
    const store = useAuthStore()

    await store.restore()

    expect(store.status).toBe('anonymous')
    expect(store.user).toBeNull()
    expect(store.isAuthenticated).toBe(false)
    expect(store.restoreError).toBeNull()
  })

  it('auth store restore records network error', async () => {
    mock.onGet('/admin/auth/me').networkError()
    const store = useAuthStore()

    await store.restore()

    expect(store.status).toBe('anonymous')
    expect(store.restoreError).toBeInstanceOf(ApiError)
    expect((store.restoreError as ApiError).code).toBe('network_error')
  })

  it('auth store restore dedupes concurrent calls', async () => {
    mock.onGet('/admin/auth/me').reply(200, makeUser())
    const store = useAuthStore()

    await Promise.all([store.restore(), store.restore()])
    expect(mock.history.get).toHaveLength(1)

    await store.restore()
    expect(mock.history.get).toHaveLength(1)
    expect(store.status).toBe('authenticated')
  })

  it('auth store login success and failure', async () => {
    mock.onPost('/admin/auth/login').replyOnce(401, {
      error: { code: 'invalid_credentials', message: '帳號或密碼錯誤', details: null },
    })
    const store = useAuthStore()
    store.reset()

    const err = await store.login('clerk01', 'wrong').catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).code).toBe('invalid_credentials')
    expect(store.status).toBe('anonymous')
    expect(store.user).toBeNull()

    mock.onPost('/admin/auth/login').replyOnce(200, { user: makeUser() })
    const me = await store.login('clerk01', 'Passw0rd123')

    expect(me.username).toBe('clerk01')
    expect(store.status).toBe('authenticated')
    expect(JSON.parse(mock.history.post[1]?.data as string)).toEqual({ username: 'clerk01', password: 'Passw0rd123' })
  })

  it('auth store logout resets even when api fails', async () => {
    mock.onPost('/admin/auth/logout').reply(500, {
      error: { code: 'internal_error', message: '伺服器錯誤', details: null },
    })
    const store = useAuthStore()
    store.setUser(makeUser())
    expect(store.status).toBe('authenticated')

    await store.logout()

    expect(store.user).toBeNull()
    expect(store.status).toBe('anonymous')
    expect(store.hasPermission('students:read')).toBe(false)
    expect(mock.history.post[0]?.url).toBe('/admin/auth/logout')
  })

  it('auth store changePassword clears must change flag', async () => {
    mock.onPost('/admin/auth/change-password').reply(200, { user: makeUser({ must_change_password: false }) })
    const store = useAuthStore()
    store.setUser(makeUser({ must_change_password: true }))
    expect(store.mustChangePassword).toBe(true)

    const me = await store.changePassword('Old0000000a', 'New0000000a')

    expect(me.must_change_password).toBe(false)
    expect(store.mustChangePassword).toBe(false)
    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({
      current_password: 'Old0000000a',
      new_password: 'New0000000a',
    })
  })

  it('auth store hasAnyPermission', () => {
    const store = useAuthStore()
    store.setUser(makeUser({ permissions: ['exams:read'] }))

    expect(store.hasAnyPermission(['exams:write', 'exams:read'])).toBe(true)
    expect(store.hasAnyPermission(['exams:write'])).toBe(false)
    expect(store.hasAnyPermission([])).toBe(false)
  })
})
