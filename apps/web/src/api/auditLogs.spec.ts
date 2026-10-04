import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { listAuditLogs } from './auditLogs'
import { adminHttp } from './http'

describe('auditLogsApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('auditLogsApi passes filters', async () => {
    const params = {
      action_prefix: 'staff_user.',
      date_from: '2026-10-01',
      date_to: '2026-10-02',
      page: 1,
      page_size: 50,
    }
    mock.onGet('/admin/audit-logs', { params }).reply(200, {
      items: [
        {
          id: 'a1',
          action: 'staff_user.update',
          actor_name: '王主任',
          before: { display_name: '陳師' },
          after: { display_name: '陳老師' },
        },
      ],
      total: 1,
    })

    const page = await listAuditLogs(params)

    expect(page.total).toBe(1)
    expect(page.items[0]?.after).toEqual({ display_name: '陳老師' })
    expect(mock.history.get[0]?.params).toEqual(params)
  })

  it('auditLogsApi surfaces validation error', async () => {
    mock.onGet('/admin/audit-logs').reply(422, {
      error: { code: 'validation_error', message: '開始日不可晚於結束日', details: null },
    })

    const err = await listAuditLogs({ date_from: '2026-10-03', date_to: '2026-10-02' }).catch(
      (e: unknown) => e,
    )

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).code).toBe('validation_error')
  })
})
