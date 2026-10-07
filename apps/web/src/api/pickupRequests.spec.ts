import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { adminHttp } from './http'
import {
  acknowledgePickupRequest,
  cancelPickupRequest,
  completePickupRequest,
  createPickupRequest,
  fetchPickupQueue,
  fetchPickupRoster,
  replyPickupRequest,
  type InvalidPickupStatusDetails,
  type PickupRequest,
  type PickupRequestExistsDetails,
  type PickupRoster,
} from './pickupRequests'

const ARRIVED: PickupRequest = {
  id: 'r1',
  student: { id: 's1', student_no: 'A001', name: '王小明', grade_level: 3, class_id: 'c1', class_name: '三年甲班' },
  service_date: '2026-10-02',
  source: 'parent',
  requested_by_type: 'parent',
  requested_by_name: '王媽媽',
  expected_arrival_at: '17:30',
  status: 'arrived',
  homework_status_at_request: 'in_progress',
  current_homework_status: 'in_progress',
  current_ready_eta: '17:45',
  reply_ready_eta: '17:45',
  reply_message: '數學訂正中，17:45 可接',
  reply_source: 'staff',
  replied_at: '2026-10-02T09:10:00Z',
  replied_by_name: '林行政',
  needs_reply: false,
  arrived_at: '2026-10-02T09:40:00Z',
  completed_at: null,
  completed_by_name: null,
  completion_method: null,
  picked_up_by_name: null,
  cancelled_at: null,
  cancel_reason: null,
  created_at: '2026-10-02T09:00:00Z',
}

const COMPLETED: PickupRequest = {
  ...ARRIVED,
  status: 'completed',
  completed_at: '2026-10-02T09:50:00Z',
  completed_by_name: '林行政',
  completion_method: 'guardian',
  picked_up_by_name: '王媽媽',
}

const ROSTER: PickupRoster = {
  date: '2026-10-02',
  classes: [
    {
      class_id: 'c1',
      class_name: '三年甲班',
      students: [
        {
          student_id: 's1',
          student_no: 'A001',
          name: '王小明',
          grade_level: 3,
          attendance_status: 'present',
          check_in_at: '2026-10-02T08:30:00Z',
          check_out_at: null,
          leave_type: null,
          homework_status: 'in_progress',
          ready_eta: '17:45',
          open_request: { id: 'r1', status: 'pending', expected_arrival_at: '17:30', needs_reply: true },
          active_authorization_count: 0,
        },
        {
          student_id: 's2',
          student_no: 'A002',
          name: '林小華',
          grade_level: 3,
          attendance_status: 'leave',
          check_in_at: null,
          check_out_at: null,
          leave_type: 'sick',
          homework_status: null,
          ready_eta: null,
          open_request: null,
          active_authorization_count: 1,
        },
      ],
    },
    { class_id: null, class_name: '未分班', students: [] },
  ],
}

function conflict(code: string, message: string, details: unknown = null) {
  return { error: { code, message, details } }
}

async function apiErrorOf(p: Promise<unknown>): Promise<ApiError> {
  const err = await p.catch((e: unknown) => e)
  expect(err).toBeInstanceOf(ApiError)
  return err as ApiError
}

describe('pickupRequestsApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('pickupRequestsApi queue and roster', async () => {
    mock.onGet('/admin/pickup/queue', { params: { date: '2026-10-02' } }).reply(200, {
      date: '2026-10-02',
      open: [ARRIVED],
      closed: [],
      counts: { pending: 0, acknowledged: 0, arrived: 1, needs_reply: 0 },
    })
    mock.onGet('/admin/pickup/roster', { params: { class_id: 'c1' } }).reply(200, ROSTER)

    const queue = await fetchPickupQueue('2026-10-02')
    const roster = await fetchPickupRoster({ class_id: 'c1' })

    expect(queue.counts.arrived).toBe(1)
    expect(queue.open[0]).toMatchObject({ id: 'r1', status: 'arrived', expected_arrival_at: '17:30' })
    expect(queue.open[0]?.student.name).toBe('王小明')
    expect(queue.closed).toEqual([])
    expect(roster.classes[0]?.students[0]?.open_request?.status).toBe('pending')
    expect(roster).toEqual(ROSTER)
    expect(roster.classes[0]?.students[1]).toMatchObject({
      leave_type: 'sick',
      open_request: null,
      active_authorization_count: 1,
    })
    expect(roster.classes[1]).toMatchObject({ class_id: null, class_name: '未分班' })
    expect(mock.history.get.map((call) => call.url)).toEqual(['/admin/pickup/queue', '/admin/pickup/roster'])
    expect(mock.history.get[0]?.params).toEqual({ date: '2026-10-02' })
    expect(mock.history.get[1]?.params).toEqual({ class_id: 'c1' })
  })

  it('pickupRequestsApi queue and roster default to today', async () => {
    mock.onGet('/admin/pickup/queue').reply(200, {
      date: '2026-10-03',
      open: [],
      closed: [],
      counts: { pending: 0, acknowledged: 0, arrived: 0, needs_reply: 0 },
    })
    mock.onGet('/admin/pickup/roster').reply(200, { date: '2026-10-03', classes: [] })

    const queue = await fetchPickupQueue()
    const roster = await fetchPickupRoster()

    expect(queue.date).toBe('2026-10-03')
    expect(roster.classes).toEqual([])
    // 沒給日期就不帶 date，由後端以今天為準
    expect(mock.history.get.map((call) => adminHttp.getUri(call))).toEqual([
      '/api/admin/pickup/queue',
      '/api/admin/pickup/roster',
    ])
  })

  it('pickupRequestsApi create and reply bodies', async () => {
    const replied: PickupRequest = { ...ARRIVED, status: 'pending', reply_source: 'staff', arrived_at: null }
    mock
      .onPost('/admin/pickup/requests')
      .replyOnce(201, { ...ARRIVED, source: 'staff', requested_by_type: 'staff', status: 'pending', arrived_at: null })
      .onPost('/admin/pickup/requests')
      .replyOnce(201, { ...ARRIVED, source: 'staff', requested_by_type: 'staff', expected_arrival_at: null })
      .onPost('/admin/pickup/requests')
      .replyOnce(409, conflict('pickup_request_exists', '今天已有進行中的接送請求', { request_id: 'r0', status: 'pending' }))
    mock.onPost('/admin/pickup/requests/r1/reply').replyOnce(200, replied)

    const created = await createPickupRequest({ student_id: 's1', expected_arrival_at: '17:30' })
    const createdWithoutEta = await createPickupRequest({ student_id: 's1' })
    const exists = await apiErrorOf(createPickupRequest({ student_id: 's1' }))
    const reply = await replyPickupRequest('r1', { reply_ready_eta: '17:45', reply_message: '數學訂正中，17:45 可接' })

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({ student_id: 's1', expected_arrival_at: '17:30' })
    expect(JSON.parse(mock.history.post[1]?.data as string)).toEqual({ student_id: 's1' })
    expect(mock.history.post[3]?.url).toBe('/admin/pickup/requests/r1/reply')
    expect(JSON.parse(mock.history.post[3]?.data as string)).toEqual({
      reply_ready_eta: '17:45',
      reply_message: '數學訂正中，17:45 可接',
    })
    expect(created.source).toBe('staff')
    expect(createdWithoutEta.expected_arrival_at).toBeNull()
    expect(reply.reply_source).toBe('staff')
    expect(exists.status).toBe(409)
    expect(exists.code).toBe('pickup_request_exists')
    expect(exists.details as PickupRequestExistsDetails).toEqual({ request_id: 'r0', status: 'pending' })
  })

  it('pickupRequestsApi reply sends only the given field', async () => {
    mock.onPost('/admin/pickup/requests/r1/reply').reply(200, ARRIVED)

    await replyPickupRequest('r1', { reply_message: '請到側門等候' })
    await replyPickupRequest('r1', { reply_ready_eta: '18:00' })

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({ reply_message: '請到側門等候' })
    expect(JSON.parse(mock.history.post[1]?.data as string)).toEqual({ reply_ready_eta: '18:00' })
  })

  it('pickupRequestsApi acknowledge posts without body', async () => {
    mock
      .onPost('/admin/pickup/requests/r1/acknowledge')
      .replyOnce(200, { ...ARRIVED, status: 'acknowledged', arrived_at: null })
      .onPost('/admin/pickup/requests/r1/acknowledge')
      .replyOnce(409, conflict('invalid_pickup_status', '接送請求狀態已變更，無法執行此操作', { current_status: 'arrived' }))

    const acknowledged = await acknowledgePickupRequest('r1')
    const err = await apiErrorOf(acknowledgePickupRequest('r1'))

    expect(mock.history.post[0]?.url).toBe('/admin/pickup/requests/r1/acknowledge')
    expect(mock.history.post[0]?.data).toBeUndefined()
    expect(acknowledged.status).toBe('acknowledged')
    expect(err.code).toBe('invalid_pickup_status')
    expect((err.details as InvalidPickupStatusDetails).current_status).toBe('arrived')
  })

  it('pickupRequestsApi complete with guardian or override', async () => {
    mock
      .onPost('/admin/pickup/requests/r1/complete')
      .replyOnce(200, COMPLETED)
      .onPost('/admin/pickup/requests/r1/complete')
      .replyOnce(200, { ...COMPLETED, completion_method: 'override', picked_up_by_name: null })
      .onPost('/admin/pickup/requests/r1/complete')
      .replyOnce(409, conflict('invalid_pickup_status', '接送請求狀態已變更，無法執行此操作', { current_status: 'completed' }))

    const byGuardian = await completePickupRequest('r1', { method: 'guardian', guardian_id: 'g1' })
    const byOverride = await completePickupRequest('r1', { method: 'override', note: '阿嬤臨時來接，已電話確認' })
    const err = await apiErrorOf(completePickupRequest('r1', { method: 'guardian', guardian_id: 'g1' }))

    expect(mock.history.post.map((call) => call.url)).toEqual(Array(3).fill('/admin/pickup/requests/r1/complete'))
    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({ method: 'guardian', guardian_id: 'g1' })
    expect(JSON.parse(mock.history.post[1]?.data as string)).toEqual({ method: 'override', note: '阿嬤臨時來接，已電話確認' })
    expect([byGuardian.status, byGuardian.completion_method, byGuardian.picked_up_by_name]).toEqual(['completed', 'guardian', '王媽媽'])
    expect([byOverride.completion_method, byOverride.picked_up_by_name]).toEqual(['override', null])
    expect(err.status).toBe(409)
    expect((err.details as InvalidPickupStatusDetails).current_status).toBe('completed')
  })

  it('pickupRequestsApi complete surfaces guardian and permission errors', async () => {
    mock
      .onPost('/admin/pickup/requests/r1/complete')
      .replyOnce(422, conflict('invalid_guardian', '監護人不屬於此學生'))
      .onPost('/admin/pickup/requests/r1/complete')
      .replyOnce(409, conflict('guardian_cannot_pickup', '此監護人未被設定為可接送'))
      .onPost('/admin/pickup/requests/r1/complete')
      .replyOnce(403, conflict('permission_denied', '沒有權限執行此操作', { required: ['pickup:override'] }))

    const invalid = await apiErrorOf(completePickupRequest('r1', { method: 'guardian', guardian_id: 'g9' }))
    const cannot = await apiErrorOf(completePickupRequest('r1', { method: 'guardian', guardian_id: 'g2' }))
    const denied = await apiErrorOf(completePickupRequest('r1', { method: 'override', note: '主管確認交付' }))

    expect([invalid.status, invalid.code]).toEqual([422, 'invalid_guardian'])
    expect([cannot.status, cannot.code]).toEqual([409, 'guardian_cannot_pickup'])
    expect([denied.status, denied.code]).toEqual([403, 'permission_denied'])
    expect(denied.details).toEqual({ required: ['pickup:override'] })
  })

  it('pickupRequestsApi cancel sends reason or empty body', async () => {
    const cancelled: PickupRequest = {
      ...ARRIVED,
      status: 'cancelled',
      cancelled_at: '2026-10-02T09:45:00Z',
      cancel_reason: '家長改明天',
    }
    mock.onPost('/admin/pickup/requests/r1/cancel').replyOnce(200, { ...cancelled, cancel_reason: null })
    mock.onPost('/admin/pickup/requests/r1/cancel').replyOnce(200, cancelled)

    const plain = await cancelPickupRequest('r1')
    const withReason = await cancelPickupRequest('r1', '家長改明天')

    expect(mock.history.post[0]?.data).toBe('{}')
    expect(JSON.parse(mock.history.post[1]?.data as string)).toEqual({ reason: '家長改明天' })
    expect(plain.cancel_reason).toBeNull()
    expect([withReason.status, withReason.cancel_reason]).toEqual(['cancelled', '家長改明天'])
  })

  it('pickupRequestsApi blank reason is omitted', async () => {
    mock.onPost('/admin/pickup/requests/r1/cancel').reply(200, ARRIVED)

    await cancelPickupRequest('r1', '')
    await cancelPickupRequest('r1', '   ')

    expect(mock.history.post.map((call) => call.data)).toEqual(['{}', '{}'])
  })

  it('pickupRequestsApi encodes ids in paths', async () => {
    mock.onPost(/\/admin\/pickup\/requests\/r%2F1\/(reply|acknowledge|complete|cancel)$/).reply(200, ARRIVED)

    await replyPickupRequest('r/1', { reply_message: '好' })
    await acknowledgePickupRequest('r/1')
    await completePickupRequest('r/1', { method: 'guardian', guardian_id: 'g1' })
    await cancelPickupRequest('r/1')

    expect(mock.history.post.map((call) => call.url)).toEqual([
      '/admin/pickup/requests/r%2F1/reply',
      '/admin/pickup/requests/r%2F1/acknowledge',
      '/admin/pickup/requests/r%2F1/complete',
      '/admin/pickup/requests/r%2F1/cancel',
    ])
  })
})
