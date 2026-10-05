import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError, type ValidationErrorItem } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { adminHttp } from './http'
import { listSettings, updateSetting } from './settings'

const PICKUP_WINDOW = {
  request_start: '12:00',
  request_end: '19:00',
  latest_expected_arrival: '19:00',
  auto_expire_minutes: 120,
}

describe('settingsApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('settingsApi lists settings items', async () => {
    mock.onGet('/admin/settings').reply(200, {
      items: [
        {
          key: 'org.profile',
          group: 'org',
          label: '安親班資料',
          is_secret: false,
          value: { name: '快樂安親班' },
          json_schema: { type: 'object' },
          updated_at: null,
          updated_by_name: null,
        },
      ],
    })

    const items = await listSettings()

    expect(items).toHaveLength(1)
    expect(items[0]?.key).toBe('org.profile')
    expect(items[0]?.value).toEqual({ name: '快樂安親班' })
  })

  it('settingsApi updates with value wrapper', async () => {
    mock.onPut('/admin/settings/pickup.window').reply(200, {
      key: 'pickup.window',
      group: 'pickup',
      label: '接送時段',
      is_secret: false,
      value: PICKUP_WINDOW,
      json_schema: { type: 'object' },
      updated_at: '2026-10-02T02:00:00Z',
      updated_by_name: '王主任',
    })

    const setting = await updateSetting('pickup.window', PICKUP_WINDOW)

    expect(setting.key).toBe('pickup.window')
    expect(mock.history.put).toHaveLength(1)
    expect(mock.history.put[0]?.url).toBe('/admin/settings/pickup.window')
    expect(JSON.parse(mock.history.put[0]?.data as string)).toEqual({ value: PICKUP_WINDOW })
  })

  it('settingsApi encodes key in path', async () => {
    mock.onPut('/admin/settings/a%2Fb').reply(200, { key: 'a/b' })

    await updateSetting('a/b', {})

    expect(mock.history.put[0]?.url).toBe('/admin/settings/a%2Fb')
  })

  it('settingsApi surfaces invalid setting value', async () => {
    mock.onPut('/admin/settings/pickup.window').reply(422, {
      error: {
        code: 'invalid_setting_value',
        message: '設定值不合法',
        details: [{ loc: ['auto_expire_minutes'], msg: '需介於 10~600', type: 'x' }],
      },
    })

    const err = await updateSetting('pickup.window', { ...PICKUP_WINDOW, auto_expire_minutes: 5 }).catch(
      (e: unknown) => e,
    )

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(422)
    expect((err as ApiError).code).toBe('invalid_setting_value')
    expect(((err as ApiError).details as ValidationErrorItem[])[0]?.loc).toEqual(['auto_expire_minutes'])
  })
})
