import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { fetchDashboardToday } from './dashboard'
import { adminHttp } from './http'

describe('dashboardApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('dashboardApi fetches today', async () => {
    mock.onGet('/admin/dashboard/today').reply(200, {
      date: '2026-10-02',
      is_service_day: true,
      attendance: { expected_total: 80, arrived: 70, present: 60, left: 10, not_arrived: 4, leave: 5, absent: 1 },
      pickup: { open: 3, needs_reply: 1, arrived: 1, completed: 12 },
      homework: { total: 70, done: 40, in_progress: 20, not_started: 10, completion_rate: 0.571 },
      recent_leaves: [],
    })

    const today = await fetchDashboardToday()

    expect(today.homework.completion_rate).toBe(0.571)
    expect(today.attendance.not_arrived).toBe(4)
    expect(today.recent_leaves).toEqual([])
  })

  it('dashboardApi surfaces permission error', async () => {
    mock.onGet('/admin/dashboard/today').reply(403, {
      error: { code: 'permission_denied', message: '沒有權限', details: { required: 'dashboard:read' } },
    })

    const err = await fetchDashboardToday().catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(403)
    expect((err as ApiError).code).toBe('permission_denied')
  })
})
