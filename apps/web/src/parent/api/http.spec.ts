import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { parentHttp, parentRefreshHttp, setUnauthenticatedHandler } from './http'

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

describe('parentHttp', () => {
  let mock: MockAdapter
  let refreshMock: MockAdapter
  let handler: ReturnType<typeof vi.fn<() => void>>

  beforeEach(() => {
    mock = createApiMock(parentHttp)
    refreshMock = createApiMock(parentRefreshHttp)
    handler = vi.fn<() => void>()
    setUnauthenticatedHandler(handler)
  })

  afterEach(() => {
    mock.restore()
    refreshMock.restore()
  })

  it('parentHttp refreshes once then retries', async () => {
    mock
      .onGet('/parent/children')
      .replyOnce(401, UNAUTHORIZED)
      .onGet('/parent/children')
      .replyOnce(200, [{ id: 'c1' }])
    refreshMock.onPost('/parent/auth/refresh').reply(200, { parent: {} })

    const res = await parentHttp.get('/parent/children')

    expect(res.data).toEqual([{ id: 'c1' }])
    expect(refreshMock.history.post.length).toBe(1)
    expect(handler).toHaveBeenCalledTimes(0)
  })

  it('parentHttp calls unauthenticated handler when refresh fails', async () => {
    mock.onGet('/parent/me').reply(401, UNAUTHORIZED)
    refreshMock.onPost('/parent/auth/refresh').reply(401, UNAUTHORIZED)

    const err = await apiErrorOf(parentHttp.get('/parent/me'))

    expect(err.status).toBe(401)
    expect(handler).toHaveBeenCalledTimes(1)
    expect(mock.history.get.length).toBe(1)
  })

  it('parentHttp treats refresh 409 as rotated', async () => {
    mock
      .onGet('/parent/children')
      .replyOnce(401, UNAUTHORIZED)
      .onGet('/parent/children')
      .replyOnce(200, [{ id: 'c1' }])
    refreshMock.onPost('/parent/auth/refresh').reply(409, {
      error: { code: 'refresh_in_progress', message: '登入狀態更新中' },
    })

    const res = await parentHttp.get('/parent/children')

    expect(res.data).toEqual([{ id: 'c1' }])
    expect(mock.history.get.length).toBe(2)
    expect(handler).toHaveBeenCalledTimes(0)
  })

  it('parentHttp skips refresh for login endpoints', async () => {
    mock.onPost('/parent/auth/liff-login').reply(401, {
      error: { code: 'invalid_id_token', message: 'LINE 登入失敗' },
    })

    const err = await apiErrorOf(parentHttp.post('/parent/auth/liff-login', { id_token: 'x' }))

    expect(err.code).toBe('invalid_id_token')
    expect(refreshMock.history.post.length).toBe(0)
    expect(handler).toHaveBeenCalledTimes(0)
  })

  it('parentHttp skips refresh for bind', async () => {
    mock.onPost('/parent/auth/bind').reply(401, UNAUTHORIZED)

    const err = await apiErrorOf(parentHttp.post('/parent/auth/bind', { code: '123456' }))

    expect(err.status).toBe(401)
    expect(refreshMock.history.post.length).toBe(0)
    expect(handler).toHaveBeenCalledTimes(0)
  })

  it('parentHttp exposes business error code', async () => {
    mock.onPost('/parent/pickup/requests').reply(409, {
      error: { code: 'pickup_request_exists', message: '今天已有接送請求', details: { request_id: 'r1' } },
    })

    const err = await apiErrorOf(parentHttp.post('/parent/pickup/requests', { student_id: 's1' }))

    expect(err.status).toBe(409)
    expect(err.code).toBe('pickup_request_exists')
    expect((err.details as { request_id: string }).request_id).toBe('r1')
    expect(refreshMock.history.post.length).toBe(0)
  })
})
