import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createApiMock } from '@/test/helpers'
import { parentHttp } from './http'
import { listNotifications, markAllNotificationsRead, markNotificationRead } from './notifications'

const NOTIFICATION = {
  id: 'n1',
  event: 'homework.done',
  title: '作業完成',
  body: '王小明今天的作業都完成了',
  payload: { student_id: 's1' },
  read_at: '2026-10-05T09:00:00Z',
  created_at: '2026-10-05T08:30:00Z',
  deep_link: '#/children/s1/homework',
}

describe('notificationsApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(parentHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('notificationsApi list params', async () => {
    const page = { items: [], total: 0, unread_count: 0 }
    mock.onGet('/parent/notifications').reply(200, page)

    const first = await listNotifications()
    await listNotifications({ unreadOnly: true, page: 2 })
    await listNotifications({ unreadOnly: false, pageSize: 50 })

    expect(first).toEqual(page)
    expect(mock.history.get[0]!.params).toEqual({ page: 1, page_size: 20 })
    expect(mock.history.get[1]!.params).toEqual({ unread_only: true, page: 2, page_size: 20 })
    expect(mock.history.get[2]!.params).toEqual({ page: 1, page_size: 50 })
  })

  it('notificationsApi read paths', async () => {
    mock.onPost('/parent/notifications/n1/read').reply(200, NOTIFICATION)
    mock.onPost('/parent/notifications/read-all').reply(200, { updated: 3 })

    const read = await markNotificationRead('n1')
    const all = await markAllNotificationsRead()

    expect(read).toEqual(NOTIFICATION)
    expect(all.updated).toBe(3)
    expect(mock.history.post.map((r) => r.url)).toEqual([
      '/parent/notifications/n1/read',
      '/parent/notifications/read-all',
    ])
  })
})
