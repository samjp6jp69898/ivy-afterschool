import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError, type Page } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { parentHttp, parentRefreshHttp } from './http'
import {
  type LeaveAttachment,
  type ParentLeave,
  cancelLeave,
  createLeave,
  listChildLeaves,
  uploadLeaveAttachment,
} from './leaves'

const UNAUTHORIZED = { error: { code: 'unauthenticated', message: '請重新登入', details: null } }
const EVIL_ID = '../../me?x=1#y'
const UUID = '3f2b8c1e-5a4d-4c7e-9b0a-1d2e3f4a5b6c'

const ATTACHMENT: LeaveAttachment = {
  id: 'a1',
  mime_type: 'application/pdf',
  size_bytes: 20480,
  created_at: '2026-10-05T01:00:00Z',
  url: 'https://files.example.test/leave-attachments/a1.pdf?sig=abc',
}

const LEAVE: ParentLeave = {
  id: 'l1',
  student_id: 's1',
  leave_type: 'sick',
  leave_type_label: '病假',
  start_date: '2026-10-05',
  end_date: '2026-10-06',
  reason: '發燒',
  status: 'active',
  created_by_type: 'parent',
  created_at: '2026-10-04T12:00:00Z',
  cancelled_at: null,
  can_cancel: true,
  attachments: [ATTACHMENT],
}

const CANCELLED: ParentLeave = {
  ...LEAVE,
  status: 'cancelled',
  cancelled_at: '2026-10-04T13:00:00Z',
  can_cancel: false,
}

const NEW_LEAVE = {
  student_id: 's1',
  leave_type: 'sick',
  start_date: '2026-10-05',
  end_date: '2026-10-06',
} as const

async function apiErrorOf(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError)
    return e as ApiError
  }
  throw new Error('預期 reject 但 resolve 了')
}

describe('leavesApi', () => {
  let mock: MockAdapter
  let refreshMock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(parentHttp)
    refreshMock = createApiMock(parentRefreshHttp)
  })

  afterEach(() => {
    mock.restore()
    refreshMock.restore()
  })

  /** 第 index 個 POST 實際送出的 JSON body */
  function postBody(index: number): unknown {
    return JSON.parse(mock.history.post[index]!.data as string)
  }

  function newFile(): File {
    return new File(['x'], 'note.pdf', { type: 'application/pdf' })
  }

  it('leavesApi lists with paging', async () => {
    const page: Page<ParentLeave> = { items: [LEAVE], total: 21 }
    mock.onGet('/parent/children/s1/leaves').reply(200, page)

    const second = await listChildLeaves('s1', 2)
    await listChildLeaves('s1')

    expect(second).toEqual(page)
    expect(second.total).toBe(21)
    expect(mock.history.get.map((r) => r.url)).toEqual(['/parent/children/s1/leaves', '/parent/children/s1/leaves'])
    expect(mock.history.get[0]!.params).toEqual({ page: 2, page_size: 20 })
    expect(mock.history.get[1]!.params).toEqual({ page: 1, page_size: 20 })
  })

  it('leavesApi list propagates 404', async () => {
    mock.onGet('/parent/children/s9/leaves').reply(404, {
      error: { code: 'student_not_found', message: '找不到這位學生', details: null },
    })

    const err = await apiErrorOf(listChildLeaves('s9'))

    expect(err.status).toBe(404)
    expect(err.code).toBe('student_not_found')
  })

  it('leavesApi create omits empty reason', async () => {
    mock.onPost('/parent/leaves').reply(409, {
      error: {
        code: 'leave_overlap',
        message: '這段期間已有請假',
        details: { leave_id: 'l0', start_date: '2026-10-05', end_date: '2026-10-07' },
      },
    })

    const err = await apiErrorOf(createLeave({ ...NEW_LEAVE, reason: '' }))

    expect(mock.history.post.map((r) => r.url)).toEqual(['/parent/leaves'])
    expect(postBody(0)).toEqual(NEW_LEAVE)
    expect('reason' in (postBody(0) as object)).toBe(false)
    expect(err.status).toBe(409)
    expect(err.code).toBe('leave_overlap')
    expect((err.details as { end_date: string }).end_date).toBe('2026-10-07')
  })

  it('leavesApi create omits blank reason', async () => {
    mock.onPost('/parent/leaves').reply(201, LEAVE)

    await createLeave({ ...NEW_LEAVE })
    await createLeave({ ...NEW_LEAVE, reason: '   ' })

    expect(postBody(0)).toEqual(NEW_LEAVE)
    expect(postBody(1)).toEqual(NEW_LEAVE)
    expect('reason' in (postBody(1) as object)).toBe(false)
  })

  it('leavesApi create sends the given fields', async () => {
    mock.onPost('/parent/leaves').reply(201, LEAVE)

    const created = await createLeave({ ...NEW_LEAVE, leave_type: 'personal', reason: '家裡有事' })

    expect(postBody(0)).toEqual({ ...NEW_LEAVE, leave_type: 'personal', reason: '家裡有事' })
    expect(created).toEqual(LEAVE)
  })

  it('leavesApi upload sends file field', async () => {
    mock
      .onPost('/parent/leaves/l1/attachments')
      .replyOnce(201, ATTACHMENT)
      .onPost('/parent/leaves/l1/attachments')
      .replyOnce(413, { error: { code: 'file_too_large', message: '檔案超過 10 MB', details: null } })

    const uploaded = await uploadLeaveAttachment('l1', newFile())
    const err = await apiErrorOf(uploadLeaveAttachment('l1', newFile()))

    expect(mock.history.post.map((r) => r.url)).toEqual([
      '/parent/leaves/l1/attachments',
      '/parent/leaves/l1/attachments',
    ])
    const form = mock.history.post[0]!.data as FormData
    expect((form.get('file') as File).name).toBe('note.pdf')
    expect((form.get('file') as File).type).toBe('application/pdf')
    expect(uploaded).toEqual(ATTACHMENT)
    expect(err.status).toBe(413)
    expect(err.code).toBe('file_too_large')
  })

  it('leavesApi upload sends only the file field as multipart', async () => {
    mock.onPost('/parent/leaves/l1/attachments').reply(201, ATTACHMENT)

    await uploadLeaveAttachment('l1', newFile())

    const request = mock.history.post[0]!
    // 不是 JSON 字串：parentHttp 預設的 application/json 會讓 axios 把 FormData 轉成 JSON、檔案遺失
    expect(request.data).toBeInstanceOf(FormData)
    expect(String(request.headers?.['Content-Type'])).not.toContain('application/json')
    expect(Array.from((request.data as FormData).keys())).toEqual(['file'])
  })

  it('leavesApi upload resends multipart after session refresh', async () => {
    mock
      .onPost('/parent/leaves/l1/attachments')
      .replyOnce(401, UNAUTHORIZED)
      .onPost('/parent/leaves/l1/attachments')
      .replyOnce(201, ATTACHMENT)
    refreshMock.onPost('/parent/auth/refresh').reply(200, { parent: {} })

    const uploaded = await uploadLeaveAttachment('l1', newFile())

    expect(uploaded).toEqual(ATTACHMENT)
    expect(refreshMock.history.post).toHaveLength(1)
    expect(mock.history.post).toHaveLength(2)
    for (const request of mock.history.post) {
      expect(request.data).toBeInstanceOf(FormData)
      expect(((request.data as FormData).get('file') as File).name).toBe('note.pdf')
    }
  })

  it('leavesApi upload surfaces business errors', async () => {
    mock
      .onPost('/parent/leaves/l1/attachments')
      .replyOnce(409, { error: { code: 'attachment_limit_reached', message: '附件數量已達上限', details: null } })
      .onPost('/parent/leaves/l1/attachments')
      .replyOnce(415, { error: { code: 'unsupported_file_type', message: '不支援的檔案格式', details: null } })

    const limit = await apiErrorOf(uploadLeaveAttachment('l1', newFile()))
    const type = await apiErrorOf(uploadLeaveAttachment('l1', newFile()))

    expect(limit.status).toBe(409)
    expect(limit.code).toBe('attachment_limit_reached')
    expect(type.status).toBe(415)
    expect(type.code).toBe('unsupported_file_type')
  })

  it('leavesApi cancel path', async () => {
    mock.onPost('/parent/leaves/l1/cancel').reply(200, CANCELLED)

    const cancelled = await cancelLeave('l1')

    expect(cancelled).toEqual(CANCELLED)
    expect(cancelled.status).toBe('cancelled')
    expect(mock.history.post.map((r) => r.url)).toEqual(['/parent/leaves/l1/cancel'])
    expect(mock.history.post[0]!.data).toBeUndefined()
  })

  it('leavesApi cancel surfaces business errors', async () => {
    mock.onPost('/parent/leaves/l1/cancel').reply(409, {
      error: { code: 'leave_already_ended', message: '這筆請假的日子都已過去', details: null },
    })

    const err = await apiErrorOf(cancelLeave('l1'))

    expect(err.status).toBe(409)
    expect(err.code).toBe('leave_already_ended')
  })

  it('leavesApi keeps null attachment url', async () => {
    const unsigned: LeaveAttachment = { ...ATTACHMENT, id: 'a2', url: null }
    const withUnsigned: ParentLeave = { ...LEAVE, attachments: [ATTACHMENT, unsigned] }
    mock.onGet('/parent/children/s1/leaves').reply(200, { items: [withUnsigned], total: 1 })
    mock.onPost('/parent/leaves/l1/cancel').reply(200, { ...CANCELLED, attachments: [unsigned] })
    mock.onPost('/parent/leaves/l1/attachments').reply(201, unsigned)

    const list = await listChildLeaves('s1')
    const cancelled = await cancelLeave('l1')
    const uploaded = await uploadLeaveAttachment('l1', newFile())

    expect(list.items[0]!.attachments.map((a) => a.url)).toEqual([ATTACHMENT.url, null])
    expect(list.items[0]!.attachments[1]).toEqual(unsigned)
    expect(cancelled.attachments[0]!.url).toBeNull()
    expect(uploaded).toEqual(unsigned)
    expect(uploaded.url).toBeNull()
  })

  it('leavesApi encodes path ids', async () => {
    mock.onAny().reply(200, { items: [], total: 0 })
    const encoded = encodeURIComponent(EVIL_ID)

    await listChildLeaves(EVIL_ID)
    await cancelLeave(EVIL_ID)
    await uploadLeaveAttachment(EVIL_ID, newFile())
    await listChildLeaves(UUID)
    await cancelLeave(UUID)
    await uploadLeaveAttachment(UUID, newFile())

    const evilUrls = [mock.history.get[0]!.url, mock.history.post[0]!.url, mock.history.post[1]!.url]
    expect(evilUrls).toEqual([
      `/parent/children/${encoded}/leaves`,
      `/parent/leaves/${encoded}/cancel`,
      `/parent/leaves/${encoded}/attachments`,
    ])
    // 編碼後不含未編碼的 '?'、'#'，路徑層數與一般 UUID 相同
    const uuidUrls = [mock.history.get[1]!.url, mock.history.post[2]!.url, mock.history.post[3]!.url]
    expect(uuidUrls).toEqual([
      `/parent/children/${UUID}/leaves`,
      `/parent/leaves/${UUID}/cancel`,
      `/parent/leaves/${UUID}/attachments`,
    ])
    evilUrls.forEach((url, i) => {
      expect(url).not.toMatch(/[?#]/)
      expect(url!.split('/')).toHaveLength(uuidUrls[i]!.split('/').length)
    })
  })
})
