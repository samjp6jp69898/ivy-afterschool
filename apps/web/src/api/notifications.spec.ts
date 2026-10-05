import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError, type Notification } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { adminHttp } from './http'
import { listNotifications, markAllNotificationsRead, markNotificationRead } from './notifications'

const PICKUP: Notification = {
  id: 'n1',
  event: 'pickup.requested',
  title: '王小明家長發起接送',
  body: '預計 17:30 抵達',
  payload: {},
  read_at: null,
  created_at: '2026-10-02T08:00:00Z',
  deep_link: '/pickup',
}

describe('notificationsApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('notificationsApi lists with params', async () => {
    const params = { unread_only: true, page: 1, page_size: 20 }
    mock.onGet('/admin/notifications', { params }).reply(200, { items: [PICKUP], total: 1, unread_count: 1 })

    const page = await listNotifications(params)

    expect(page.unread_count).toBe(1)
    expect(page.total).toBe(1)
    expect(page.items[0]?.deep_link).toBe('/pickup')
    expect(mock.history.get[0]?.params).toEqual(params)
  })

  it('notificationsApi marks read and read-all', async () => {
    mock.onPost('/admin/notifications/n1/read').reply(200, { ...PICKUP, read_at: '2026-10-02T08:01:00Z' })
    mock.onPost('/admin/notifications/read-all').reply(200, { updated: 3 })

    const read = await markNotificationRead('n1')
    const all = await markAllNotificationsRead()

    expect(read.read_at).toBe('2026-10-02T08:01:00Z')
    expect(all).toEqual({ updated: 3 })
    expect(mock.history.post.map((c) => c.url)).toEqual([
      '/admin/notifications/n1/read',
      '/admin/notifications/read-all',
    ])
  })

  it('notificationsApi surfaces not found', async () => {
    mock.onPost('/admin/notifications/zzz/read').reply(404, {
      error: { code: 'notification_not_found', message: '找不到通知', details: null },
    })

    const err = await markNotificationRead('zzz').catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(404)
    expect((err as ApiError).code).toBe('notification_not_found')
  })
})
