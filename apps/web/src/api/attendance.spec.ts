import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { filenameFromDisposition } from '@/shared/http/download'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import {
  amendAttendance,
  batchCheckIn,
  checkIn,
  checkOut,
  exportMonthlyAttendance,
  fetchDailyAttendance,
  fetchMonthlyAttendance,
  markAbsent,
  type AttendanceRow,
  type MonthlyAttendance,
} from './attendance'
import { adminHttp } from './http'

const PRESENT: AttendanceRow = {
  id: 'a1',
  student_id: 's1',
  student_no: 'A001',
  student_name: '王小明',
  grade_level: 3,
  class_id: 'c1',
  class_name: '三年甲班',
  service_date: '2026-10-02',
  status: 'present',
  check_in_at: '2026-10-02T09:30:00Z',
  check_in_source: 'manual',
  check_out_at: null,
  check_out_source: null,
  leave: null,
  note: null,
  updated_at: '2026-10-02T09:30:00Z',
}

/** 營業日尚未建立出勤列的學生：虛擬列（id 與 updated_at 為 null） */
const VIRTUAL: AttendanceRow = {
  ...PRESENT,
  id: null,
  student_id: 's2',
  student_no: 'A002',
  student_name: '林小華',
  status: 'expected',
  check_in_at: null,
  check_in_source: null,
  updated_at: null,
}

const LEFT: AttendanceRow = {
  ...PRESENT,
  status: 'left',
  check_out_at: '2026-10-02T10:20:00Z',
  check_out_source: 'manual',
  note: '家長提早接',
}

const ABSENT: AttendanceRow = {
  ...VIRTUAL,
  id: 'a2',
  status: 'absent',
  note: '家長來電請假未通知',
  updated_at: '2026-10-02T09:00:00Z',
}

const MONTHLY: MonthlyAttendance = {
  month: '2026-09',
  class_id: 'c1',
  class_name: '三年甲班',
  // 範例只放 3 天；實際長度等於當月天數
  days: [
    { date: '2026-09-04', weekday: 4, is_service_day: true },
    { date: '2026-09-05', weekday: 5, is_service_day: false },
    { date: '2026-09-07', weekday: 0, is_service_day: true },
  ],
  students: [
    {
      student_id: 's1',
      student_no: 'A001',
      name: '王小明',
      class_name: '三年甲班',
      statuses: ['present', null, 'leave'],
      stats: { service_days: 2, attended: 1, absent: 0, leave: 1, unrecorded: 0 },
    },
    {
      student_id: 's2',
      student_no: 'A002',
      name: '林小華',
      class_name: '三年甲班',
      statuses: ['absent', null, 'left'],
      stats: { service_days: 2, attended: 1, absent: 1, leave: 0, unrecorded: 0 },
    },
  ],
  // 全部學生 stats 的加總：service_days 是應出勤人次（2 位學生各 2 個營業日 = 4），不是營業日數（2）
  totals: { service_days: 4, attended: 2, absent: 1, leave: 1, unrecorded: 0 },
}

function conflict(code: string, message: string, details: unknown = null) {
  return { error: { code, message, details } }
}

async function apiErrorOf(p: Promise<unknown>): Promise<ApiError> {
  const err = await p.catch((e: unknown) => e)
  expect(err).toBeInstanceOf(ApiError)
  return err as ApiError
}

describe('attendanceApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('attendanceApi daily with filters', async () => {
    const params = { date: '2026-10-02', class_id: 'c1' }
    mock.onGet('/admin/attendance/daily', { params }).reply(200, {
      date: '2026-10-02',
      is_service_day: true,
      summary: { total: 2, expected: 1, present: 1, left: 0, absent: 0, leave: 0 },
      items: [PRESENT, VIRTUAL],
    })

    const daily = await fetchDailyAttendance(params)

    expect(daily.summary.present).toBe(1)
    expect(daily.summary).toEqual({ total: 2, expected: 1, present: 1, left: 0, absent: 0, leave: 0 })
    expect(daily.is_service_day).toBe(true)
    expect(daily.items.map((row) => row.id)).toEqual(['a1', null])
    expect(daily.items[1]).toMatchObject({ status: 'expected', check_in_at: null, updated_at: null })
    expect(mock.history.get[0]?.url).toBe('/admin/attendance/daily')
    expect(mock.history.get[0]?.params).toEqual(params)
  })

  it('attendanceApi daily passes status filter and works without params', async () => {
    mock.onGet('/admin/attendance/daily', { params: { status: 'absent' } }).reply(200, {
      date: '2026-10-02',
      is_service_day: true,
      // summary 以篩選前的全部列計算，不受 status 篩選影響
      summary: { total: 2, expected: 0, present: 1, left: 0, absent: 1, leave: 0 },
      items: [ABSENT],
    })
    mock.onGet('/admin/attendance/daily').reply(200, {
      date: '2026-10-03',
      is_service_day: false,
      summary: { total: 0, expected: 0, present: 0, left: 0, absent: 0, leave: 0 },
      items: [],
    })

    const absentOnly = await fetchDailyAttendance({ status: 'absent' })
    const weekend = await fetchDailyAttendance()

    expect(absentOnly.items.map((row) => row.status)).toEqual(['absent'])
    expect(absentOnly.summary.total).toBe(2)
    expect(weekend).toMatchObject({ is_service_day: false, items: [] })
    expect(mock.history.get.map((call) => call.url)).toEqual(['/admin/attendance/daily', '/admin/attendance/daily'])
  })

  it('attendanceApi check-in sends empty body without note', async () => {
    mock
      .onPost('/admin/attendance/s1/check-in')
      .replyOnce(200, PRESENT)
      .onPost('/admin/attendance/s1/check-in')
      .replyOnce(200, { ...PRESENT, note: '遲到 10 分' })
      .onPost('/admin/attendance/s1/check-in')
      .replyOnce(409, conflict('already_checked_in', '學生已到班'))

    const plain = await checkIn('s1')
    const withNote = await checkIn('s1', '遲到 10 分')
    const err = await apiErrorOf(checkIn('s1'))

    expect(plain.status).toBe('present')
    expect(mock.history.post[0]?.data).toBe('{}')
    expect(JSON.parse(mock.history.post[1]?.data as string)).toEqual({ note: '遲到 10 分' })
    expect(withNote.note).toBe('遲到 10 分')
    expect(mock.history.post.map((call) => call.url)).toEqual(Array(3).fill('/admin/attendance/s1/check-in'))
    expect(err.status).toBe(409)
    expect(err.code).toBe('already_checked_in')
    expect(err.message).toBe('學生已到班')
  })

  it('attendanceApi check-out and mark-absent send note or empty body', async () => {
    mock
      .onPost('/admin/attendance/s1/check-out')
      .replyOnce(200, { ...LEFT, note: null })
      .onPost('/admin/attendance/s1/check-out')
      .replyOnce(200, LEFT)
      .onPost('/admin/attendance/s1/check-out')
      .replyOnce(409, conflict('not_checked_in', '學生尚未到班'))
    mock
      .onPost('/admin/attendance/s2/mark-absent')
      .replyOnce(200, { ...ABSENT, note: null })
      .onPost('/admin/attendance/s2/mark-absent')
      .replyOnce(200, ABSENT)
      .onPost('/admin/attendance/s2/mark-absent')
      .replyOnce(409, conflict('already_checked_in', '學生已到班，請改走改判'))

    const out = await checkOut('s1')
    const outWithNote = await checkOut('s1', '家長提早接')
    const notCheckedIn = await apiErrorOf(checkOut('s1'))
    const absent = await markAbsent('s2')
    const absentWithNote = await markAbsent('s2', '家長來電請假未通知')
    const alreadyIn = await apiErrorOf(markAbsent('s2'))

    expect(out.status).toBe('left')
    expect(outWithNote.note).toBe('家長提早接')
    expect(absent.status).toBe('absent')
    expect(absentWithNote.note).toBe('家長來電請假未通知')
    expect(mock.history.post.map((call) => call.url)).toEqual([
      '/admin/attendance/s1/check-out',
      '/admin/attendance/s1/check-out',
      '/admin/attendance/s1/check-out',
      '/admin/attendance/s2/mark-absent',
      '/admin/attendance/s2/mark-absent',
      '/admin/attendance/s2/mark-absent',
    ])
    expect(mock.history.post[0]?.data).toBe('{}')
    expect(JSON.parse(mock.history.post[1]?.data as string)).toEqual({ note: '家長提早接' })
    expect(mock.history.post[3]?.data).toBe('{}')
    expect(JSON.parse(mock.history.post[4]?.data as string)).toEqual({ note: '家長來電請假未通知' })
    expect(notCheckedIn.code).toBe('not_checked_in')
    expect(alreadyIn.code).toBe('already_checked_in')
    expect(alreadyIn.status).toBe(409)
  })

  it('attendanceApi blank note is omitted', async () => {
    // 空白備註原樣送出會把既有備註覆蓋成空字串（後端 note = coalesce(:note, note)）
    mock.onPost(/\/admin\/attendance\/s1\/(check-in|check-out|mark-absent)$/).reply(200, PRESENT)

    await checkIn('s1', '')
    await checkOut('s1', '   ')
    await markAbsent('s1', '')

    expect(mock.history.post.map((call) => call.data)).toEqual(['{}', '{}', '{}'])
  })

  it('attendanceApi batch check-in returns skipped', async () => {
    mock
      .onPost('/admin/attendance/batch-check-in')
      .replyOnce(200, {
        succeeded: [PRESENT],
        skipped: [{ student_id: 's2', code: 'student_on_leave', message: '學生今日請假' }],
      })
      .onPost('/admin/attendance/batch-check-in')
      .replyOnce(409, conflict('not_service_day', '今天不是營業日'))

    const result = await batchCheckIn(['s1', 's2'])
    const err = await apiErrorOf(batchCheckIn(['s1']))

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({ student_ids: ['s1', 's2'] })
    expect(result.succeeded.map((row) => [row.student_id, row.status])).toEqual([['s1', 'present']])
    expect(result.skipped[0]?.code).toBe('student_on_leave')
    expect(result.skipped[0]).toEqual({ student_id: 's2', code: 'student_on_leave', message: '學生今日請假' })
    // 部分略過仍是 200；只有整批錯誤（非營業日）才會 reject
    expect(err.status).toBe(409)
    expect(err.code).toBe('not_service_day')
  })

  it('attendanceApi amend patches with reason', async () => {
    const body = { status: 'expected' as const, reason: '誤按到班' }
    mock
      .onPatch('/admin/attendance/a1')
      .replyOnce(200, { ...PRESENT, status: 'expected', check_in_at: null, check_in_source: null })
      .onPatch('/admin/attendance/a1')
      .replyOnce(409, conflict('attendance_managed_by_leave', '請假中的出勤只能由請假的建立或取消改變'))

    const amended = await amendAttendance('a1', body)
    const err = await apiErrorOf(amendAttendance('a1', body))

    expect(JSON.parse(mock.history.patch[0]?.data as string)).toEqual(body)
    expect(mock.history.patch[0]?.url).toBe('/admin/attendance/a1')
    expect(amended.status).toBe('expected')
    expect(amended.check_in_at).toBeNull()
    expect(err.status).toBe(409)
    expect(err.code).toBe('attendance_managed_by_leave')
  })

  it('attendanceApi amend sends explicit null to clear fields', async () => {
    // 省略 = 沿用原值；null = 清空（後端以 model_fields_set 區分）
    const body = { check_out_at: null, note: null, reason: '更正離班紀錄' }
    mock.onPatch('/admin/attendance/a1').reply(200, PRESENT)

    await amendAttendance('a1', body)
    await amendAttendance('a1', { check_in_at: '2026-10-02T09:00:00Z', reason: '補登到班時間' })

    const sent = mock.history.patch.map((call) => JSON.parse(call.data as string) as Record<string, unknown>)
    expect(sent[0]).toEqual(body)
    expect(Object.keys(sent[0]!).sort()).toEqual(['check_out_at', 'note', 'reason'])
    expect(sent[1]).toEqual({ check_in_at: '2026-10-02T09:00:00Z', reason: '補登到班時間' })
    expect(Object.keys(sent[1]!).sort()).toEqual(['check_in_at', 'reason'])
  })

  it('attendanceApi amend surfaces 422 codes', async () => {
    const cases = [
      ['check_in_required', '已到班需要到班時間'],
      ['check_out_required', '已離班需要到班與離班時間'],
      ['invalid_times', '離班時間不可早於到班時間'],
      ['time_not_on_service_date', '時間必須在該出勤日當天'],
      ['no_changes', '內容與原紀錄相同'],
    ] as const

    for (const [code, message] of cases) {
      mock.onPatch('/admin/attendance/a1').replyOnce(422, conflict(code, message))

      const err = await apiErrorOf(amendAttendance('a1', { status: 'left', reason: '補登離班' }))

      expect(err.status).toBe(422)
      expect([err.code, err.message]).toEqual([code, message])
    }
    expect(mock.history.patch).toHaveLength(cases.length)
  })

  it('attendanceApi monthly report', async () => {
    const params = { month: '2026-09', class_id: 'c1' }
    mock.onGet('/admin/attendance/monthly', { params }).reply(200, MONTHLY)

    const report = await fetchMonthlyAttendance(params)

    expect(report.month).toBe('2026-09')
    expect(report.days).toHaveLength(3)
    expect(report.days[1]).toEqual({ date: '2026-09-05', weekday: 5, is_service_day: false })
    // statuses 與 days 等長、同順序；null = 非營業日或無紀錄
    expect(report.students[0]?.statuses).toEqual(['present', null, 'leave'])
    expect(report.students[1]?.statuses).toHaveLength(report.days.length)
    expect(report.students[0]?.stats).toEqual({ service_days: 2, attended: 1, absent: 0, leave: 1, unrecorded: 0 })
    expect(report.totals).toEqual({ service_days: 4, attended: 2, absent: 1, leave: 1, unrecorded: 0 })
    expect(mock.history.get[0]?.url).toBe('/admin/attendance/monthly')
    expect(mock.history.get[0]?.params).toEqual(params)
  })

  it('attendanceApi export returns blob response', async () => {
    const disposition =
      "attachment; filename*=UTF-8''%E5%87%BA%E5%8B%A4%E6%9C%88%E5%A0%B1_%E5%85%A8%E9%83%A8_2026-09.xlsx"
    const xlsx = new Blob(['xlsx'], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
    mock
      .onGet('/admin/attendance/monthly/export', { params: { month: '2026-09' } })
      .reply(200, xlsx, { 'content-disposition': disposition })

    const res = await exportMonthlyAttendance({ month: '2026-09' })

    expect(res.data).toBeInstanceOf(Blob)
    expect(res.headers['content-disposition']).toBe(disposition)
    // 回完整 response，呼叫端才能用 FRONTEND-007 的 saveBlobResponse 取檔名
    expect(filenameFromDisposition(res.headers['content-disposition'] as string, 'fallback.xlsx')).toBe(
      '出勤月報_全部_2026-09.xlsx',
    )
    expect(mock.history.get[0]?.url).toBe('/admin/attendance/monthly/export')
    expect(mock.history.get[0]?.params).toEqual({ month: '2026-09' })
    expect(mock.history.get[0]?.responseType).toBe('blob')
  })

  it('attendanceApi export sends class filter', async () => {
    mock.onGet('/admin/attendance/monthly/export', { params: { month: '2026-09', class_id: 'c1' } }).reply(200, new Blob(['x']), {
      'content-disposition': 'attachment; filename="report.xlsx"',
    })

    const res = await exportMonthlyAttendance({ month: '2026-09', class_id: 'c1' })

    expect(res.status).toBe(200)
    expect(mock.history.get[0]?.params).toEqual({ month: '2026-09', class_id: 'c1' })
  })

  it('attendanceApi encodes ids in paths', async () => {
    mock.onPost(/\/admin\/attendance\/s%2F1\/(check-in|check-out|mark-absent)$/).reply(200, PRESENT)
    mock.onPatch('/admin/attendance/a%201').reply(200, PRESENT)

    await checkIn('s/1')
    await checkOut('s/1')
    await markAbsent('s/1')
    await amendAttendance('a 1', { note: '備註', reason: '補註記' })

    expect(mock.history.post.map((call) => call.url)).toEqual([
      '/admin/attendance/s%2F1/check-in',
      '/admin/attendance/s%2F1/check-out',
      '/admin/attendance/s%2F1/mark-absent',
    ])
    expect(mock.history.patch[0]?.url).toBe('/admin/attendance/a%201')
  })
})
