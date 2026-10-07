import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { adminHttp } from './http'
import {
  cancelLeave,
  createLeave,
  fetchLeaveAttachmentUrl,
  listLeaves,
  type Leave,
  type LeaveOverlapDetails,
} from './leaves'

const LEAVE: Leave = {
  id: 'l1',
  student: { id: 's1', student_no: 'A001', name: '王小明', class_name: '三年甲班' },
  leave_type: 'sick',
  leave_type_label: '病假',
  start_date: '2026-10-05',
  end_date: '2026-10-06',
  reason: '感冒發燒',
  status: 'active',
  created_by_type: 'parent',
  created_by_name: '王媽媽',
  created_at: '2026-10-03T02:00:00Z',
  cancelled_at: null,
  cancelled_by_type: null,
  cancelled_by_name: null,
  attachments: [{ id: 'a1', mime_type: 'application/pdf', size_bytes: 20480, created_at: '2026-10-03T02:05:00Z' }],
}

function conflict(code: string, message: string, details: unknown = null) {
  return { error: { code, message, details } }
}

async function apiErrorOf(p: Promise<unknown>): Promise<ApiError> {
  const err = await p.catch((e: unknown) => e)
  expect(err).toBeInstanceOf(ApiError)
  return err as ApiError
}

describe('leavesApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('leavesApi lists with filters', async () => {
    const params = { status: 'active' as const, date_from: '2026-10-01', date_to: '2026-10-31', page: 1, page_size: 20 }
    mock.onGet('/admin/leaves', { params }).reply(200, { items: [LEAVE], total: 1 })

    const page = await listLeaves(params)

    expect(page.items[0]?.leave_type_label).toBe('病假')
    expect(page.items[0]?.student.name).toBe('王小明')
    expect(page.items[0]?.attachments[0]).toEqual({
      id: 'a1',
      mime_type: 'application/pdf',
      size_bytes: 20480,
      created_at: '2026-10-03T02:05:00Z',
    })
    expect(page.total).toBe(1)
    expect(mock.history.get[0]?.url).toBe('/admin/leaves')
    expect(mock.history.get[0]?.params).toEqual(params)
  })

  it('leavesApi lists with full filter set', async () => {
    const params = {
      student_id: 's1',
      class_id: 'c1',
      status: 'cancelled' as const,
      leave_type: 'personal' as const,
      created_by_type: 'staff' as const,
      date_from: '2026-10-01',
      date_to: '2026-10-31',
      page: 2,
      page_size: 50,
    }
    mock.onGet('/admin/leaves', { params }).reply(200, { items: [], total: 0 })
    mock.onGet('/admin/leaves').reply(200, { items: [LEAVE], total: 1 })

    const filtered = await listLeaves(params)
    const unfiltered = await listLeaves()

    expect(filtered).toEqual({ items: [], total: 0 })
    expect(mock.history.get[0]?.params).toEqual(params)
    expect(unfiltered.total).toBe(1)
  })

  it('leavesApi create surfaces overlap details', async () => {
    const body = {
      student_id: 's1',
      leave_type: 'personal' as const,
      start_date: '2026-10-05',
      end_date: '2026-10-06',
      reason: '家庭旅遊',
    }
    const created: Leave = {
      ...LEAVE,
      id: 'l2',
      leave_type: 'personal',
      leave_type_label: '事假',
      reason: '家庭旅遊',
      created_by_type: 'staff',
      created_by_name: '林行政',
      attachments: [],
    }
    mock
      .onPost('/admin/leaves')
      .replyOnce(201, created)
      .onPost('/admin/leaves')
      .replyOnce(
        409,
        conflict('leave_overlap', '請假期間重疊', { leave_id: 'l0', start_date: '2026-10-06', end_date: '2026-10-07' }),
      )

    const ok = await createLeave(body)
    const err = await apiErrorOf(createLeave(body))

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual(body)
    expect(mock.history.post[0]?.url).toBe('/admin/leaves')
    expect(ok.id).toBe('l2')
    expect(ok.created_by_type).toBe('staff')
    expect(err.status).toBe(409)
    expect(err.code).toBe('leave_overlap')
    expect(err.message).toBe('請假期間重疊')
    const details = err.details as LeaveOverlapDetails
    expect(details.start_date).toBe('2026-10-06')
    expect(details).toEqual({ leave_id: 'l0', start_date: '2026-10-06', end_date: '2026-10-07' })
  })

  it('leavesApi create without reason sends no reason and surfaces other errors', async () => {
    const body = { student_id: 's1', leave_type: 'other' as const, start_date: '2026-10-10', end_date: '2026-10-11' }
    mock
      .onPost('/admin/leaves')
      .replyOnce(422, conflict('no_service_days_in_range', '請假期間沒有營業日'))
      .onPost('/admin/leaves')
      .replyOnce(409, conflict('student_not_active', '學生已退班或暫停'))

    const noDays = await apiErrorOf(createLeave(body))
    const inactive = await apiErrorOf(createLeave(body))

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual(body)
    expect(Object.keys(JSON.parse(mock.history.post[0]?.data as string) as object)).not.toContain('reason')
    expect([noDays.status, noDays.code]).toEqual([422, 'no_service_days_in_range'])
    expect([inactive.status, inactive.code]).toEqual([409, 'student_not_active'])
  })

  it('leavesApi cancel sends scope', async () => {
    const truncated: Leave = { ...LEAVE, end_date: '2026-10-05' }
    const cancelled: Leave = {
      ...LEAVE,
      status: 'cancelled',
      cancelled_at: '2026-10-04T01:00:00Z',
      cancelled_by_type: 'staff',
      cancelled_by_name: '林行政',
    }
    mock
      .onPost('/admin/leaves/l1/cancel')
      .replyOnce(200, truncated)
      .onPost('/admin/leaves/l1/cancel')
      .replyOnce(200, cancelled)
      .onPost('/admin/leaves/l1/cancel')
      .replyOnce(409, conflict('leave_already_ended', '這筆請假已經結束'))
      .onPost('/admin/leaves/l1/cancel')
      .replyOnce(409, conflict('leave_not_active', '這筆請假已取消'))

    const remaining = await cancelLeave('l1')
    const all = await cancelLeave('l1', 'all')
    const ended = await apiErrorOf(cancelLeave('l1'))
    const notActive = await apiErrorOf(cancelLeave('l1', 'all'))

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({ scope: 'remaining' })
    expect(JSON.parse(mock.history.post[1]?.data as string)).toEqual({ scope: 'all' })
    expect(mock.history.post.map((call) => call.url)).toEqual(Array(4).fill('/admin/leaves/l1/cancel'))
    // 取消剩餘日子：仍是 active、end_date 縮短；整筆取消：cancelled
    expect([remaining.status, remaining.end_date]).toEqual(['active', '2026-10-05'])
    expect([all.status, all.cancelled_by_name]).toEqual(['cancelled', '林行政'])
    expect([ended.status, ended.code]).toEqual([409, 'leave_already_ended'])
    expect([notActive.status, notActive.code]).toEqual([409, 'leave_not_active'])
  })

  it('leavesApi attachment url is never cached', async () => {
    mock
      .onGet('/admin/leaves/l1/attachments/a1')
      .replyOnce(200, { url: 'https://files.example.test/leave-attachments/a1.pdf?sig=first', expires_in: 300 })
      .onGet('/admin/leaves/l1/attachments/a1')
      .replyOnce(200, { url: 'https://files.example.test/leave-attachments/a1.pdf?sig=second', expires_in: 300 })

    const first = await fetchLeaveAttachmentUrl('l1', 'a1')
    const second = await fetchLeaveAttachmentUrl('l1', 'a1')

    expect(mock.history.get.length).toBe(2)
    expect(first.url).toBe('https://files.example.test/leave-attachments/a1.pdf?sig=first')
    expect(second.url).toBe('https://files.example.test/leave-attachments/a1.pdf?sig=second')
    expect(second.expires_in).toBe(300)
  })

  it('leavesApi attachment not found surfaces api error', async () => {
    mock.onGet('/admin/leaves/l1/attachments/zz').reply(404, conflict('attachment_not_found', '找不到附件'))

    const err = await apiErrorOf(fetchLeaveAttachmentUrl('l1', 'zz'))

    expect([err.status, err.code]).toEqual([404, 'attachment_not_found'])
  })

  it('leavesApi encodes ids in paths', async () => {
    mock.onPost('/admin/leaves/l%2F1/cancel').reply(200, LEAVE)
    mock.onGet('/admin/leaves/l%2F1/attachments/a%201').reply(200, { url: 'https://files.example.test/x', expires_in: 300 })

    await cancelLeave('l/1')
    await fetchLeaveAttachmentUrl('l/1', 'a 1')

    expect(mock.history.post[0]?.url).toBe('/admin/leaves/l%2F1/cancel')
    expect(mock.history.get[0]?.url).toBe('/admin/leaves/l%2F1/attachments/a%201')
  })
})
