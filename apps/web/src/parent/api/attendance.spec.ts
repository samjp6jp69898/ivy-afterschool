import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { type MonthlyAttendance, getChildAttendance } from './attendance'
import { parentHttp } from './http'

const MONTH: MonthlyAttendance = {
  student_id: 's1',
  month: '2026-10',
  days: [
    {
      date: '2026-10-01',
      is_service_day: true,
      status: 'left',
      check_in_at: '2026-10-01T08:05:00Z',
      check_out_at: '2026-10-01T10:30:00Z',
      leave_type: null,
    },
    {
      date: '2026-10-02',
      is_service_day: true,
      status: 'leave',
      check_in_at: null,
      check_out_at: null,
      leave_type: 'sick',
    },
    {
      date: '2026-10-03',
      is_service_day: false,
      status: null,
      check_in_at: null,
      check_out_at: null,
      leave_type: null,
    },
  ],
  stats: { service_days: 21, attended: 15, absent: 1, leave: 2, unrecorded: 3 },
}

async function apiErrorOf(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError)
    return e as ApiError
  }
  throw new Error('預期 reject 但 resolve 了')
}

describe('attendanceApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(parentHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('attendanceApi requests month for child', async () => {
    mock.onGet('/parent/children/s1/attendance', { params: { month: '2026-10' } }).reply(200, MONTH)

    const attendance = await getChildAttendance('s1', '2026-10')

    expect(attendance).toEqual(MONTH)
    expect(attendance.stats).toEqual({ service_days: 21, attended: 15, absent: 1, leave: 2, unrecorded: 3 })
    expect(attendance.days[1]!.leave_type).toBe('sick')
    expect(mock.history.get).toHaveLength(1)
    expect(mock.history.get[0]!.url).toBe('/parent/children/s1/attendance')
    expect(mock.history.get[0]!.params).toEqual({ month: '2026-10' })
  })

  it('attendanceApi propagates 422', async () => {
    const details = [{ loc: ['query', 'month'], msg: 'String should match pattern', type: 'string_pattern_mismatch' }]
    mock.onGet('/parent/children/s1/attendance').reply(422, {
      error: { code: 'validation_error', message: '輸入資料格式錯誤', details },
    })

    const err = await apiErrorOf(getChildAttendance('s1', '2026-13'))

    expect(err.status).toBe(422)
    expect(err.code).toBe('validation_error')
    expect(err.details).toEqual(details)
    // month 不在前端改寫或驗證，原樣送出由後端判斷
    expect(mock.history.get[0]!.params).toEqual({ month: '2026-13' })
  })

  it('attendanceApi propagates 404 for a child that is not yours', async () => {
    mock.onGet('/parent/children/s9/attendance').reply(404, {
      error: { code: 'student_not_found', message: '找不到這位學生', details: null },
    })

    const err = await apiErrorOf(getChildAttendance('s9', '2026-10'))

    expect(err.status).toBe(404)
    expect(err.code).toBe('student_not_found')
  })
})
