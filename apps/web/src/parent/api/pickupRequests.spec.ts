import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { parentHttp } from './http'
import {
  type ParentPickupRequest,
  cancelPickupRequest,
  createPickupRequest,
  listTodayPickupRequests,
  markPickupArrived,
} from './pickupRequests'

const REQUEST: ParentPickupRequest = {
  id: 'r1',
  student_id: 's1',
  student_name: '王小明',
  service_date: '2026-10-07',
  status: 'pending',
  expected_arrival_at: '17:40',
  reply_ready_eta: '17:30',
  reply_message: '已通知老師，作業預計 17:30 完成',
  reply_source: 'auto',
  replied_at: '2026-10-07T09:20:05Z',
  arrived_at: null,
  completed_at: null,
  picked_up_by_name: null,
  cancelled_at: null,
  created_at: '2026-10-07T09:20:00Z',
  can_cancel: true,
  can_mark_arrived: true,
}

const ARRIVED: ParentPickupRequest = {
  ...REQUEST,
  status: 'arrived',
  expected_arrival_at: null,
  arrived_at: '2026-10-07T09:35:00Z',
  can_mark_arrived: false,
}

const CANCELLED: ParentPickupRequest = {
  ...REQUEST,
  status: 'cancelled',
  cancelled_at: '2026-10-07T09:30:00Z',
  can_cancel: false,
  can_mark_arrived: false,
}

const DONE: ParentPickupRequest = {
  ...REQUEST,
  id: 'r2',
  student_id: 's2',
  student_name: '王小華',
  status: 'completed',
  reply_source: 'staff',
  completed_at: '2026-10-07T09:45:00Z',
  picked_up_by_name: '王媽媽',
  can_cancel: false,
  can_mark_arrived: false,
}

describe('pickupRequestsApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(parentHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  /** 第 index 個 POST 實際送出的 JSON body */
  function postBody(index: number): unknown {
    return JSON.parse(mock.history.post[index]!.data as string)
  }

  async function failureOf(call: Promise<unknown>): Promise<unknown> {
    let failure: unknown = null
    await call.catch((e: unknown) => {
      failure = e
    })
    return failure
  }

  it('pickupRequestsApi create omits empty arrival', async () => {
    mock.onPost('/parent/pickup/requests').reply(201, REQUEST)

    const created = await createPickupRequest({ student_id: 's1' })
    await createPickupRequest({ student_id: 's1', expected_arrival_at: '17:40' })
    await createPickupRequest({ student_id: 's1', expected_arrival_at: null })

    expect(created).toEqual(REQUEST)
    expect(mock.history.post.map((r) => r.url)).toEqual([
      '/parent/pickup/requests',
      '/parent/pickup/requests',
      '/parent/pickup/requests',
    ])
    expect(postBody(0)).toEqual({ student_id: 's1' })
    expect(postBody(1)).toEqual({ student_id: 's1', expected_arrival_at: '17:40' })
    expect(postBody(2)).toEqual({ student_id: 's1' })
  })

  it('pickupRequestsApi create surfaces business error', async () => {
    mock.onPost('/parent/pickup/requests').reply(409, {
      error: { code: 'pickup_window_closed', message: '目前不在可發起接送的時段', details: null },
    })

    const failure = await failureOf(createPickupRequest({ student_id: 's1' }))

    expect(failure).toBeInstanceOf(ApiError)
    expect((failure as ApiError).status).toBe(409)
    expect((failure as ApiError).code).toBe('pickup_window_closed')
    expect((failure as ApiError).message).toBe('目前不在可發起接送的時段')
  })

  it('pickupRequestsApi arrived and cancel paths', async () => {
    mock
      .onPost('/parent/pickup/requests/r1/arrived')
      .reply(200, ARRIVED)
      .onPost('/parent/pickup/requests/r1/cancel')
      .reply(200, CANCELLED)

    const arrived = await markPickupArrived('r1')
    const cancelled = await cancelPickupRequest('r1')
    await cancelPickupRequest('r1', '臨時有事')

    expect(arrived).toEqual(ARRIVED)
    expect(cancelled).toEqual(CANCELLED)
    expect(mock.history.post.map((r) => r.url)).toEqual([
      '/parent/pickup/requests/r1/arrived',
      '/parent/pickup/requests/r1/cancel',
      '/parent/pickup/requests/r1/cancel',
    ])
    expect(mock.history.post[0]!.data).toBeUndefined()
    expect(postBody(1)).toEqual({})
    expect(postBody(2)).toEqual({ reason: '臨時有事' })
  })

  it('pickupRequestsApi cancel omits blank reason', async () => {
    mock.onPost('/parent/pickup/requests/r1/cancel').reply(200, REQUEST)

    await cancelPickupRequest('r1', '')
    await cancelPickupRequest('r1', '   ')

    expect(postBody(0)).toEqual({})
    expect(postBody(1)).toEqual({})
  })

  it('pickupRequestsApi lists today', async () => {
    mock.onGet('/parent/pickup/requests/today').reply(200, [REQUEST, DONE])

    const items = await listTodayPickupRequests()

    expect(items).toHaveLength(2)
    expect(items).toEqual([REQUEST, DONE])
    expect(items[1]!.picked_up_by_name).toBe('王媽媽')
    expect(mock.history.get.map((r) => r.url)).toEqual(['/parent/pickup/requests/today'])
  })

  it('pickupRequestsApi arrive now body', async () => {
    mock
      .onPost('/parent/pickup/requests')
      .replyOnce(201, ARRIVED)
      .onPost('/parent/pickup/requests')
      .replyOnce(409, {
        error: {
          code: 'pickup_request_exists',
          message: '已有進行中的接送請求',
          details: { request_id: 'r7', status: 'pending' },
        },
      })

    const created = await createPickupRequest({ student_id: 's1', arrived: true })
    const failure = await failureOf(createPickupRequest({ student_id: 's1', arrived: true }))

    expect(postBody(0)).toEqual({ student_id: 's1', arrived: true })
    expect(postBody(1)).toEqual({ student_id: 's1', arrived: true })
    expect(created.status).toBe('arrived')
    expect(failure).toBeInstanceOf(ApiError)
    expect((failure as ApiError).code).toBe('pickup_request_exists')
    expect((failure as ApiError).details).toEqual({ request_id: 'r7', status: 'pending' })
  })

  it('pickupRequestsApi create forwards both fields when the type is bypassed', async () => {
    mock.onPost('/parent/pickup/requests').reply(422, {
      error: { code: 'validation_error', message: '輸入資料格式錯誤', details: [] },
    })

    const conflicting = { student_id: 's1', arrived: true, expected_arrival_at: '17:40' } as const
    // @ts-expect-error arrived 與 expected_arrival_at 在型別層互斥；刻意繞過，確認 client 不自行丟掉其中一個
    const failure = await failureOf(createPickupRequest(conflicting))

    expect(postBody(0)).toEqual({ student_id: 's1', expected_arrival_at: '17:40', arrived: true })
    expect(failure).toBeInstanceOf(ApiError)
    expect((failure as ApiError).code).toBe('validation_error')
  })
})
