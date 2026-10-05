import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import {
  DEFAULT_PARENT_LIMITS,
  _resetPublicConfigForTests,
  getParentLimits,
  getPublicConfig,
} from './config'
import { parentHttp } from './http'

const LIMITS = {
  leave_past_days: 30,
  leave_future_days: 60,
  leave_max_attachments: 3,
  leave_max_attachment_mb: 10,
  authorization_max_days_ahead: 14,
  persons_max: 10,
}

const CONFIG = {
  liff_id: '1650000000-abc',
  org_name: '快樂安親班',
  org_phone: '02-2345-6789',
  logo_url: null,
  add_friend_url: 'https://line.me/R/ti/p/@happy',
  limits: LIMITS,
}

const UNAVAILABLE = { error: { code: 'service_unavailable', message: '服務暫時無法使用', details: null } }

describe('publicConfig', () => {
  let mock: MockAdapter

  beforeEach(() => {
    _resetPublicConfigForTests()
    mock = createApiMock(parentHttp)
  })

  afterEach(() => {
    mock.restore()
    _resetPublicConfigForTests()
  })

  it('publicConfig fetches once', async () => {
    mock.onGet('/parent/config').reply(200, CONFIG)

    const [first, second] = await Promise.all([getPublicConfig(), getPublicConfig()])
    const third = await getPublicConfig()

    expect(first).toEqual(CONFIG)
    expect(second).toEqual(CONFIG)
    expect(third).toEqual(CONFIG)
    expect(mock.history.get.length).toBe(1)
  })

  it('publicConfig retries after failure', async () => {
    mock
      .onGet('/parent/config')
      .replyOnce(503, UNAVAILABLE)
      .onGet('/parent/config')
      .replyOnce(200, CONFIG)

    let failure: unknown = null
    await getPublicConfig().catch((e: unknown) => {
      failure = e
    })
    expect(failure).toBeInstanceOf(ApiError)
    expect((failure as ApiError).status).toBe(503)

    const config = await getPublicConfig()

    expect(config.org_name).toBe('快樂安親班')
    expect(mock.history.get.length).toBe(2)
  })

  it('publicConfig limits fall back to defaults', async () => {
    mock
      .onGet('/parent/config')
      .replyOnce(500, UNAVAILABLE)
      .onGet('/parent/config')
      .replyOnce(200, { ...CONFIG, limits: { ...LIMITS, leave_future_days: 90 } })

    expect(await getParentLimits()).toEqual(DEFAULT_PARENT_LIMITS)
    expect(DEFAULT_PARENT_LIMITS).toEqual(LIMITS)

    const limits = await getParentLimits()

    expect(limits.leave_future_days).toBe(90)
    expect(mock.history.get.length).toBe(2)
  })
})
