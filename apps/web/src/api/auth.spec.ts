import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { changePassword, fetchMe, fetchPublicConfig, login, logout, refresh } from './auth'
import { adminHttp } from './http'

const CLERK = {
  id: 'u1',
  username: 'clerk01',
  display_name: '林行政',
  role: { id: 'r1', code: 'clerk', name: '行政' },
  permissions: ['students:read'],
  must_change_password: false,
}

describe('authApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('authApi login posts credentials and unwraps user', async () => {
    mock.onPost('/admin/auth/login').reply(200, { user: CLERK })

    const me = await login({ username: 'clerk01', password: 'Passw0rd123' })

    expect(me.username).toBe('clerk01')
    expect(me.permissions).toEqual(['students:read'])
    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({
      username: 'clerk01',
      password: 'Passw0rd123',
    })
  })

  it('authApi login surfaces throttle error', async () => {
    mock.onPost('/admin/auth/login').reply(429, {
      error: { code: 'too_many_attempts', message: '嘗試次數過多，請 15 分鐘後再試', details: null },
    })

    const err = await login({ username: 'clerk01', password: 'x' }).catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(429)
    expect((err as ApiError).code).toBe('too_many_attempts')
  })

  it('authApi changePassword posts body and returns user', async () => {
    const body = { current_password: 'Old0000000a', new_password: 'New0000000a' }
    mock.onPost('/admin/auth/change-password').replyOnce(200, { user: { ...CLERK, must_change_password: false } })

    const me = await changePassword(body)

    expect(me.must_change_password).toBe(false)
    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual(body)

    mock.onPost('/admin/auth/change-password').replyOnce(422, {
      error: { code: 'weak_password', message: '密碼強度不足', details: { reasons: ['too_short'] } },
    })
    const err = await changePassword(body).catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).code).toBe('weak_password')
    expect((err as ApiError).details).toEqual({ reasons: ['too_short'] })
  })

  it('authApi fetchMe refresh logout and public config hit correct paths', async () => {
    mock.onGet('/admin/auth/me').reply(200, CLERK)
    mock.onPost('/admin/auth/refresh').reply(200, { user: { ...CLERK, display_name: '林行政２' } })
    mock.onPost('/admin/auth/logout').reply(200, { message: '已登出' })
    mock.onGet('/parent/config').reply(200, {
      liff_id: '1650000000-abc',
      org_name: '快樂安親班',
      logo_url: null,
    })

    expect((await fetchMe()).display_name).toBe('林行政')
    expect((await refresh()).display_name).toBe('林行政２')
    await expect(logout()).resolves.toBeUndefined()
    expect(await fetchPublicConfig()).toEqual({ liff_id: '1650000000-abc', org_name: '快樂安親班', logo_url: null })

    expect(mock.history.post.map((c) => c.url)).toEqual(['/admin/auth/refresh', '/admin/auth/logout'])
    expect(mock.history.get.map((c) => c.url)).toEqual(['/admin/auth/me', '/parent/config'])
  })
})
