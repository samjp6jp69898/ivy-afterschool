import { flushPromises } from '@vue/test-utils'
import axios, { type AxiosInstance } from 'axios'
import type MockAdapter from 'axios-mock-adapter'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { createHttpClient, type HttpClientOptions } from './createHttpClient'

const ADMIN_NO_REFRESH = ['/admin/auth/login', '/admin/auth/refresh']

function deferred<T = void>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function setup(overrides: Partial<HttpClientOptions> = {}) {
  const refresh = vi.fn<() => Promise<void>>(() => Promise.resolve())
  const onAuthFailure = vi.fn()
  const onPasswordChangeRequired = vi.fn()
  const client = createHttpClient({
    refresh,
    noRefreshPaths: ADMIN_NO_REFRESH,
    onAuthFailure,
    onPasswordChangeRequired,
    ...overrides,
  })
  const mock = createApiMock(client)
  return { client, mock, refresh, onAuthFailure, onPasswordChangeRequired }
}

async function rejectionOf(p: Promise<unknown>): Promise<unknown> {
  try {
    await p
  } catch (e) {
    return e
  }
  throw new Error('預期 reject 但 resolve 了')
}

async function apiErrorOf(p: Promise<unknown>): Promise<ApiError> {
  const err = await rejectionOf(p)
  expect(err).toBeInstanceOf(ApiError)
  return err as ApiError
}

const UNAUTHORIZED = { error: { code: 'unauthenticated', message: '請重新登入', details: null } }

function reply401Then200(mock: MockAdapter, url: string, body: unknown = { ok: url }): void {
  mock.onGet(url).replyOnce(401, UNAUTHORIZED).onGet(url).replyOnce(200, body)
}

describe('createHttpClient', () => {
  it('createHttpClient normalizes error envelope', async () => {
    const { client, mock } = setup()
    mock.onGet('/admin/x').reply(409, {
      error: { code: 'leave_overlap', message: '請假期間重疊', details: { leave_id: 'l1' } },
    })

    const err = await apiErrorOf(client.get('/admin/x'))

    expect(err.status).toBe(409)
    expect(err.code).toBe('leave_overlap')
    expect(err.message).toBe('請假期間重疊')
    expect(err.details).toEqual({ leave_id: 'l1' })
  })

  it('createHttpClient normalizes envelope without details to null', async () => {
    const { client, mock } = setup()
    mock.onGet('/admin/x').reply(404, { error: { code: 'student_not_found', message: '找不到學生' } })

    const err = await apiErrorOf(client.get('/admin/x'))

    expect(err.details).toBeNull()
    expect(err.code).toBe('student_not_found')
  })

  it('createHttpClient normalizes non-envelope response', async () => {
    const { client, mock } = setup()
    mock.onGet('/admin/x').reply(502, '<html>bad gateway</html>')

    const err = await apiErrorOf(client.get('/admin/x'))

    expect(err.status).toBe(502)
    expect(err.code).toBe('http_502')
    expect(err.message).toBe('伺服器暫時無法回應（502）')
  })

  it('createHttpClient normalizes network error and timeout', async () => {
    const { client, mock } = setup()
    mock.onGet('/admin/net').networkError()
    mock.onGet('/admin/slow').timeout()

    const net = await apiErrorOf(client.get('/admin/net'))
    const slow = await apiErrorOf(client.get('/admin/slow'))

    expect([net.status, net.code, net.message]).toEqual([0, 'network_error', '網路連線異常，請稍後再試'])
    expect([slow.status, slow.code, slow.message]).toEqual([0, 'timeout', '連線逾時，請稍後再試'])
  })

  it('createHttpClient parses json blob error', async () => {
    const { client, mock } = setup()
    mock
      .onGet('/admin/attendance/monthly/export')
      .reply(
        404,
        new Blob(['{"error":{"code":"class_not_found","message":"找不到班級"}}'], { type: 'application/json' }),
      )

    const err = await apiErrorOf(client.get('/admin/attendance/monthly/export', { responseType: 'blob' }))

    expect(err.status).toBe(404)
    expect(err.code).toBe('class_not_found')
    expect(err.message).toBe('找不到班級')
  })

  it('createHttpClient treats unparsable json blob as non-envelope', async () => {
    const { client, mock } = setup()
    mock.onGet('/admin/export').reply(500, new Blob(['{oops'], { type: 'application/json' }))

    const err = await apiErrorOf(client.get('/admin/export', { responseType: 'blob' }))

    expect(err.code).toBe('http_500')
  })

  it('createHttpClient passes cancellation through unchanged', async () => {
    const { client, mock } = setup()
    mock.onGet('/admin/x').reply(200, {})
    const controller = new AbortController()
    controller.abort()

    const err = await rejectionOf(client.get('/admin/x', { signal: controller.signal }))

    expect(axios.isCancel(err)).toBe(true)
    expect(err).not.toBeInstanceOf(ApiError)
  })

  it('createHttpClient refreshes once and retries on 401', async () => {
    const { client, mock, refresh, onAuthFailure } = setup()
    reply401Then200(mock, '/admin/students', { items: [], total: 0 })

    const res = await client.get('/admin/students')

    expect(res.data).toEqual({ items: [], total: 0 })
    expect(mock.history.get.length).toBe(2)
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(onAuthFailure).toHaveBeenCalledTimes(0)
  })

  it('createHttpClient shares a single refresh for concurrent 401', async () => {
    const gate = deferred()
    const { client, mock, refresh } = setup({})
    refresh.mockImplementation(() => gate.promise)
    for (const url of ['/a', '/b', '/c']) reply401Then200(mock, url)

    const pending = Promise.all(['/a', '/b', '/c'].map((u) => client.get(u)))
    await vi.waitFor(() => expect(mock.history.get.length).toBe(3))
    await flushPromises()
    gate.resolve()
    const results = await pending

    expect(results.map((r) => r.data)).toEqual([{ ok: '/a' }, { ok: '/b' }, { ok: '/c' }])
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(mock.history.get.length).toBe(6)
  })

  it('createHttpClient calls onAuthFailure when refresh fails', async () => {
    const { client, mock, refresh, onAuthFailure } = setup()
    refresh.mockRejectedValue(new ApiError(401, 'refresh_invalid', '請重新登入'))
    mock.onGet('/a').reply(401, UNAUTHORIZED)

    const err = await apiErrorOf(client.get('/a'))

    expect(err.status).toBe(401)
    expect(err.code).toBe('unauthenticated')
    expect(onAuthFailure).toHaveBeenCalledTimes(1)
    expect(mock.history.get.length).toBe(1)
  })

  it('createHttpClient calls onAuthFailure once for concurrent refresh failure', async () => {
    const gate = deferred()
    const { client, mock, refresh, onAuthFailure } = setup()
    refresh.mockImplementation(() => gate.promise)
    mock.onGet('/a').reply(401, UNAUTHORIZED)
    mock.onGet('/b').reply(401, UNAUTHORIZED)

    const pa = rejectionOf(client.get('/a'))
    const pb = rejectionOf(client.get('/b'))
    await vi.waitFor(() => expect(mock.history.get.length).toBe(2))
    await flushPromises()
    gate.reject(new Error('refresh 失敗'))
    const [ea, eb] = await Promise.all([pa, pb])

    expect((ea as ApiError).status).toBe(401)
    expect((eb as ApiError).status).toBe(401)
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(onAuthFailure).toHaveBeenCalledTimes(1)
  })

  it('createHttpClient gives up when retry is still 401', async () => {
    const { client, mock, refresh, onAuthFailure } = setup()
    mock.onGet('/a').reply(401, UNAUTHORIZED)

    const err = await apiErrorOf(client.get('/a'))

    expect(err.status).toBe(401)
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(onAuthFailure).toHaveBeenCalledTimes(1)
    expect(mock.history.get.length).toBe(2)
  })

  it('createHttpClient calls onAuthFailure once when concurrent retries are still 401', async () => {
    const gate = deferred()
    const { client, mock, refresh, onAuthFailure } = setup()
    refresh.mockImplementation(() => gate.promise)
    for (const url of ['/a', '/b', '/c']) mock.onGet(url).reply(401, UNAUTHORIZED)

    const pending = Promise.all(['/a', '/b', '/c'].map((u) => rejectionOf(client.get(u))))
    await vi.waitFor(() => expect(mock.history.get.length).toBe(3))
    await flushPromises()
    gate.resolve()
    const errors = await pending

    expect(errors.map((e) => (e as ApiError).status)).toEqual([401, 401, 401])
    expect(mock.history.get.length).toBe(6)
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(onAuthFailure).toHaveBeenCalledTimes(1)
  })

  it('createHttpClient retries once more when refresh reports refresh_in_progress', async () => {
    const inProgress = new ApiError(409, 'refresh_in_progress', '登入狀態更新中，請重試')

    const ok = setup()
    ok.refresh.mockRejectedValue(inProgress)
    reply401Then200(ok.mock, '/a')
    const res = await ok.client.get('/a')
    expect(res.data).toEqual({ ok: '/a' })
    expect(ok.onAuthFailure).toHaveBeenCalledTimes(0)

    const stillUnauthorized = setup()
    stillUnauthorized.refresh.mockRejectedValue(inProgress)
    stillUnauthorized.mock.onGet('/a').reply(401, UNAUTHORIZED)
    const err = await apiErrorOf(stillUnauthorized.client.get('/a'))
    expect(err.status).toBe(401)
    expect(stillUnauthorized.mock.history.get.length).toBe(2)
    expect(stillUnauthorized.refresh).toHaveBeenCalledTimes(1)
    expect(stillUnauthorized.onAuthFailure).toHaveBeenCalledTimes(1)
  })

  it('createHttpClient does not loop when refresh endpoint returns 401', async () => {
    for (const noRefreshPaths of [ADMIN_NO_REFRESH, ['/admin/auth/login']]) {
      let client!: AxiosInstance
      const onAuthFailure = vi.fn()
      client = createHttpClient({
        refresh: () => client.post('/admin/auth/refresh').then(() => undefined),
        noRefreshPaths,
        onAuthFailure,
      })
      const mock = createApiMock(client)
      mock.onGet('/admin/students').reply(401, UNAUTHORIZED)
      mock.onPost('/admin/auth/refresh').reply(401, {
        error: { code: 'refresh_invalid', message: '請重新登入', details: null },
      })

      const timeout = new Promise<'timeout'>((resolve) => setTimeout(() => resolve('timeout'), 1000))
      const outcome = await Promise.race([rejectionOf(client.get('/admin/students')), timeout])

      expect(outcome, `noRefreshPaths=${noRefreshPaths.join(',')}`).toBeInstanceOf(ApiError)
      expect((outcome as ApiError).status).toBe(401)
      expect(mock.history.post.length).toBe(1)
      expect(mock.history.get.length).toBe(1)
      expect(onAuthFailure).toHaveBeenCalledTimes(1)
    }
  })

  it('createHttpClient skips refresh for noRefreshPaths', async () => {
    const { client, mock, refresh, onAuthFailure } = setup()
    mock.onPost('/admin/auth/login').reply(401, {
      error: { code: 'invalid_credentials', message: '帳號或密碼錯誤' },
    })

    const err = await apiErrorOf(client.post('/admin/auth/login', { username: 'wang', password: 'x' }))

    expect(err.code).toBe('invalid_credentials')
    expect(refresh).toHaveBeenCalledTimes(0)
    expect(onAuthFailure).toHaveBeenCalledTimes(0)
  })

  it('createHttpClient keeps refresh state per instance', async () => {
    const gateA = deferred()
    const gateB = deferred()
    const a = setup()
    const b = setup()
    a.refresh.mockImplementation(() => gateA.promise)
    b.refresh.mockImplementation(() => gateB.promise)
    reply401Then200(a.mock, '/x')
    reply401Then200(b.mock, '/x')

    const pending = Promise.all([a.client.get('/x'), b.client.get('/x')])
    await vi.waitFor(() => {
      expect(a.refresh).toHaveBeenCalledTimes(1)
      expect(b.refresh).toHaveBeenCalledTimes(1)
    })
    gateA.resolve()
    gateB.resolve()
    await pending

    expect(a.refresh).toHaveBeenCalledTimes(1)
    expect(b.refresh).toHaveBeenCalledTimes(1)
  })

  it('createHttpClient triggers password change hook', async () => {
    const { client, mock, onPasswordChangeRequired, refresh } = setup()
    mock.onGet('/admin/students').reply(403, {
      error: { code: 'password_change_required', message: '請先修改密碼' },
    })
    mock.onGet('/admin/roles').reply(403, {
      error: { code: 'permission_denied', message: '沒有權限', details: { required: 'roles:read' } },
    })

    const err = await apiErrorOf(client.get('/admin/students'))
    expect(err.code).toBe('password_change_required')
    expect(onPasswordChangeRequired).toHaveBeenCalledTimes(1)

    const denied = await apiErrorOf(client.get('/admin/roles'))
    expect(denied.code).toBe('permission_denied')
    expect(onPasswordChangeRequired).toHaveBeenCalledTimes(1)
    expect(refresh).toHaveBeenCalledTimes(0)
  })

  it('createHttpClient instance defaults', () => {
    const { client } = setup()

    expect(client.defaults.baseURL).toBe('/api')
    expect(client.defaults.withCredentials).toBe(true)
    expect(client.defaults.timeout).toBe(30000)
    expect(client.defaults.headers['Content-Type']).toBe('application/json')

    const custom = createHttpClient({
      baseURL: '/api/v2',
      timeoutMs: 5000,
      refresh: () => Promise.resolve(),
      noRefreshPaths: [],
      onAuthFailure: () => undefined,
    })
    expect(custom.defaults.baseURL).toBe('/api/v2')
    expect(custom.defaults.timeout).toBe(5000)
  })
})
