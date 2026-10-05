import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { parentHttp } from './http'
import { getNotificationPreferences, updateNotificationPreferences } from './notificationPreferences'

describe('notificationPreferencesApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(parentHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('notificationPreferencesApi get unwraps items', async () => {
    mock.onGet('/parent/notification-preferences').reply(200, {
      items: [{ event: 'homework.done', label: '作業完成', line_enabled: true }],
    })

    const items = await getNotificationPreferences()

    expect(items).toHaveLength(1)
    expect(items[0]!.label).toBe('作業完成')
  })

  it('notificationPreferencesApi put partial', async () => {
    mock
      .onPut('/parent/notification-preferences')
      .replyOnce(200, { items: [{ event: 'homework.done', label: '作業完成', line_enabled: false }] })
      .onPut('/parent/notification-preferences')
      .replyOnce(422, { error: { code: 'invalid_preference_event', message: '不支援的通知類型', details: null } })

    const items = await updateNotificationPreferences([{ event: 'homework.done', line_enabled: false }])

    expect(JSON.parse(mock.history.put[0]!.data as string)).toEqual({
      items: [{ event: 'homework.done', line_enabled: false }],
    })
    expect(items).toEqual([{ event: 'homework.done', label: '作業完成', line_enabled: false }])

    let failure: unknown = null
    await updateNotificationPreferences([{ event: 'unknown.event', line_enabled: true }]).catch((e: unknown) => {
      failure = e
    })
    expect(failure).toBeInstanceOf(ApiError)
    expect((failure as ApiError).code).toBe('invalid_preference_event')
  })
})
