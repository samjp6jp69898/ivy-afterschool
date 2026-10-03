import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { adminHttp, resetAdminHttpHandlers, setAdminHttpHandlers } from './http'

const UNAUTHORIZED = { error: { code: 'unauthenticated', message: '請重新登入', details: null } }

async function apiErrorOf(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError)
    return e as ApiError
  }
  throw new Error('預期 reject 但 resolve 了')
}

describe('adminHttp', () => {
  let mock: MockAdapter

  beforeEach(() => {
    resetAdminHttpHandlers()
    mock = createApiMock(adminHttp)
    window.location.hash = ''
  })

  afterEach(() => {
    mock.restore()
    resetAdminHttpHandlers()
    window.location.hash = ''
  })

  it('adminHttp uses /api base url with credentials', () => {
    expect(adminHttp.defaults.baseURL).toBe('/api')
    expect(adminHttp.defaults.withCredentials).toBe(true)
  })

  it('adminHttp uses injected refresh then retries', async () => {
    const refresh = vi.fn(() => Promise.resolve())
    setAdminHttpHandlers({ refresh })
    mock
      .onGet('/admin/students')
      .replyOnce(401, UNAUTHORIZED)
      .onGet('/admin/students')
      .replyOnce(200, { items: [], total: 0 })

    const res = await adminHttp.get('/admin/students')

    expect(res.data.total).toBe(0)
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(mock.history.get.length).toBe(2)
  })

  it('adminHttp calls injected authFailure when refresh rejects', async () => {
    const authFailure = vi.fn()
    setAdminHttpHandlers({ refresh: () => Promise.reject(new Error('refresh 失敗')), authFailure })
    mock.onGet('/admin/classes').reply(401, UNAUTHORIZED)

    const err = await apiErrorOf(adminHttp.get('/admin/classes'))

    expect(err.status).toBe(401)
    expect(authFailure).toHaveBeenCalledTimes(1)
    expect(window.location.hash).toBe('')
  })

  it('adminHttp falls back to hash login without handlers', async () => {
    mock.onGet('/admin/classes').reply(401, UNAUTHORIZED)

    const err = await apiErrorOf(adminHttp.get('/admin/classes'))

    expect(err.status).toBe(401)
    expect(window.location.hash).toBe('#/login')
  })

  it('adminHttp reads handlers at call time', async () => {
    const first = vi.fn()
    const second = vi.fn()
    setAdminHttpHandlers({ refresh: () => Promise.reject(new Error('x')), authFailure: first })
    setAdminHttpHandlers({ authFailure: second })
    mock.onGet('/admin/classes').reply(401, UNAUTHORIZED)

    await apiErrorOf(adminHttp.get('/admin/classes'))

    expect(first).toHaveBeenCalledTimes(0)
    expect(second).toHaveBeenCalledTimes(1)
  })

  it('adminHttp does not refresh on change-password 401', async () => {
    const refresh = vi.fn(() => Promise.resolve())
    const authFailure = vi.fn()
    setAdminHttpHandlers({ refresh, authFailure })
    mock.onPost('/admin/auth/change-password').reply(401, {
      error: { code: 'unauthenticated', message: '請重新登入' },
    })

    const err = await apiErrorOf(
      adminHttp.post('/admin/auth/change-password', { current_password: 'a', new_password: 'b' }),
    )

    expect(err.code).toBe('unauthenticated')
    expect(refresh).toHaveBeenCalledTimes(0)
    expect(authFailure).toHaveBeenCalledTimes(0)
  })

  it('adminHttp never refreshes for auth endpoints', async () => {
    const refresh = vi.fn(() => Promise.resolve())
    setAdminHttpHandlers({ refresh })
    for (const path of ['/admin/auth/login', '/admin/auth/refresh', '/admin/auth/logout']) {
      mock.onPost(path).reply(401, UNAUTHORIZED)
      const err = await apiErrorOf(adminHttp.post(path))
      expect(err.status, path).toBe(401)
    }
    expect(refresh).toHaveBeenCalledTimes(0)
  })

  it('adminHttp refresh endpoint 401 inside refresh does not loop', async () => {
    const authFailure = vi.fn()
    setAdminHttpHandlers({
      refresh: () => adminHttp.post('/admin/auth/refresh').then(() => undefined),
      authFailure,
    })
    mock.onGet('/admin/students').reply(401, UNAUTHORIZED)
    mock.onPost('/admin/auth/refresh').reply(401, UNAUTHORIZED)

    const err = await apiErrorOf(adminHttp.get('/admin/students'))

    expect(err.status).toBe(401)
    expect(mock.history.post.length).toBe(1)
    expect(mock.history.get.length).toBe(1)
    expect(authFailure).toHaveBeenCalledTimes(1)
  })

  it('adminHttp routes password change required to injected handler or hash', async () => {
    mock.onGet('/admin/students').reply(403, {
      error: { code: 'password_change_required', message: '請先修改密碼' },
    })

    await apiErrorOf(adminHttp.get('/admin/students'))
    expect(window.location.hash).toBe('#/change-password')

    window.location.hash = ''
    const passwordChangeRequired = vi.fn()
    setAdminHttpHandlers({ passwordChangeRequired })
    await apiErrorOf(adminHttp.get('/admin/students'))
    expect(passwordChangeRequired).toHaveBeenCalledTimes(1)
    expect(window.location.hash).toBe('')
  })
})
