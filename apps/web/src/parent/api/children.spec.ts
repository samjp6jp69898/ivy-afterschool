import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import {
  type ChildDetail,
  type ChildSummary,
  type ChildToday,
  type ParentMe,
  getChild,
  getChildToday,
  getMe,
  listChildren,
} from './children'
import { parentHttp } from './http'

const MING: ChildSummary = {
  id: 's1',
  name: '王小明',
  grade_level: 3,
  class_name: '三年級班',
  school_name: '快樂國小',
  photo_url: 'https://files.example.test/photos/s1.jpg?sig=abc',
  status: 'active',
}

const HUA: ChildSummary = {
  id: 's2',
  name: '王小華',
  grade_level: 1,
  class_name: null,
  school_name: null,
  photo_url: null,
  status: 'suspended',
}

const ME: ParentMe = {
  id: 'p1',
  display_name: '王媽媽',
  picture_url: null,
  phone: null,
  children: [MING, HUA],
}

const HUA_DETAIL: ChildDetail = {
  ...HUA,
  school_class: '甲班',
  enrolled_on: '2025-09-01',
  my_guardian: {
    relation: 'grandparent',
    is_primary: false,
    can_pickup: false,
    receives_notifications: true,
  },
}

const TODAY: ChildToday = {
  student_id: 's2',
  date: '2026-10-02',
  is_service_day: true,
  attendance: { status: 'present', check_in_at: '2026-10-02T07:40:00Z', check_out_at: null },
  on_leave: false,
  leave: null,
  homework: { item_count: 3, done_count: 1, overall_status: 'in_progress', ready_eta: '17:30', note: null },
  pickup_request: {
    id: 'r1',
    status: 'completed',
    expected_arrival_at: '17:40',
    reply_ready_eta: '17:30',
    reply_message: '作業快完成了',
    reply_source: 'auto',
    completed_at: '2026-10-02T09:40:00Z',
    picked_up_by_name: '王媽媽',
    can_cancel: false,
    can_mark_arrived: false,
  },
}

describe('childrenApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(parentHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('childrenApi getMe returns parent with children', async () => {
    mock.onGet('/parent/me').reply(200, ME)

    const me = await getMe()

    expect(me.children[0]!.name).toBe('王小明')
    expect(me).toEqual(ME)
    expect(mock.history.get.map((r) => r.url)).toEqual(['/parent/me'])
  })

  it('childrenApi listChildren returns child summaries', async () => {
    mock.onGet('/parent/children').reply(200, [MING, HUA])

    const children = await listChildren()

    expect(children).toEqual([MING, HUA])
    expect(children[1]!.status).toBe('suspended')
    expect(mock.history.get.map((r) => r.url)).toEqual(['/parent/children'])
  })

  it('childrenApi getChild uses child id in path', async () => {
    mock.onGet('/parent/children/s2').reply(200, HUA_DETAIL)

    const child = await getChild('s2')

    expect(mock.history.get.map((r) => r.url)).toEqual(['/parent/children/s2'])
    expect(child.my_guardian.can_pickup).toBe(false)
    expect(child).toEqual(HUA_DETAIL)
  })

  it('childrenApi propagates 404', async () => {
    mock.onGet('/parent/children/s9').reply(404, {
      error: { code: 'student_not_found', message: '找不到這位學生', details: null },
    })

    let failure: unknown = null
    await getChild('s9').catch((e: unknown) => {
      failure = e
    })

    expect(failure).toBeInstanceOf(ApiError)
    expect((failure as ApiError).status).toBe(404)
    expect((failure as ApiError).code).toBe('student_not_found')
  })

  it('childrenApi getChildToday uses child id', async () => {
    mock.onGet('/parent/children/s2/today').reply(200, TODAY)

    const today = await getChildToday('s2')

    expect(mock.history.get.map((r) => r.url)).toEqual(['/parent/children/s2/today'])
    expect(today).toEqual(TODAY)
    expect(today.pickup_request?.picked_up_by_name).toBe('王媽媽')
  })

  it('childrenApi getChildToday passes through leave and missing pickup request', async () => {
    const onLeave: ChildToday = {
      student_id: 's1',
      date: '2026-10-03',
      is_service_day: true,
      attendance: { status: 'leave', check_in_at: null, check_out_at: null },
      on_leave: true,
      leave: {
        id: 'l1',
        leave_type: 'sick',
        leave_type_label: '病假',
        start_date: '2026-10-02',
        end_date: '2026-10-04',
      },
      homework: { item_count: 0, done_count: 0, overall_status: 'not_started', ready_eta: null, note: null },
      pickup_request: null,
    }
    const closedDay: ChildToday = {
      ...onLeave,
      date: '2026-10-04',
      is_service_day: false,
      attendance: { status: null, check_in_at: null, check_out_at: null },
      on_leave: false,
      leave: null,
    }
    mock
      .onGet('/parent/children/s1/today')
      .replyOnce(200, onLeave)
      .onGet('/parent/children/s1/today')
      .replyOnce(200, closedDay)

    const first = await getChildToday('s1')
    const second = await getChildToday('s1')

    expect(first).toEqual(onLeave)
    expect(first.leave?.leave_type_label).toBe('病假')
    expect(first.pickup_request).toBeNull()
    expect(second.attendance.status).toBeNull()
    expect(second.is_service_day).toBe(false)
  })
})
