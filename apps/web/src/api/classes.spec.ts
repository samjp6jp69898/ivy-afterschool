import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { archiveClass, createClass, getClass, listClasses, setClassStaff, updateClass } from './classes'
import { adminHttp } from './http'

const CLASS_A = {
  id: 'c1',
  name: '低年級A班',
  grade_levels: [1, 2],
  academic_year: 115,
  sort_order: 0,
  archived_at: null,
  student_count: 18,
  staff: [{ staff_user_id: 'u3', display_name: '陳老師', role: 'lead' }],
}

describe('classesApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('classesApi lists with query', async () => {
    const params = { academic_year: 115, include_archived: false, mine: true }
    mock.onGet('/admin/classes', { params }).reply(200, [CLASS_A])

    const classes = await listClasses(params)

    expect(classes[0]?.staff[0]?.role).toBe('lead')
    expect(mock.history.get[0]?.params).toEqual(params)
  })

  it('classesApi gets creates and updates', async () => {
    const body = { name: '低年級A班', grade_levels: [1, 2], academic_year: 115, sort_order: 0 }
    mock.onGet('/admin/classes/c1').reply(200, CLASS_A)
    mock.onPost('/admin/classes').reply(201, CLASS_A)
    mock.onPatch('/admin/classes/c1').reply(200, { ...CLASS_A, name: '低年級B班' })

    expect((await getClass('c1')).student_count).toBe(18)
    expect((await createClass(body)).id).toBe('c1')
    expect((await updateClass('c1', { name: '低年級B班' })).name).toBe('低年級B班')
    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual(body)
    expect(JSON.parse(mock.history.patch[0]?.data as string)).toEqual({ name: '低年級B班' })
  })

  it('classesApi set staff wraps items', async () => {
    const items = [
      { staff_user_id: 'u3', role: 'lead' as const },
      { staff_user_id: 'u4', role: 'assistant' as const },
    ]
    mock.onPut('/admin/classes/c1/staff').reply(200, {
      ...CLASS_A,
      staff: [...CLASS_A.staff, { staff_user_id: 'u4', display_name: '林助教', role: 'assistant' }],
    })

    const cls = await setClassStaff('c1', items)

    expect(JSON.parse(mock.history.put[0]?.data as string)).toEqual({ items })
    expect(cls.staff.map((s) => s.staff_user_id)).toEqual(['u3', 'u4'])
  })

  it('classesApi archive conflict carries student count', async () => {
    mock.onPost('/admin/classes/c1/archive').replyOnce(409, {
      error: { code: 'class_has_students', message: '班級仍有在學學生', details: { student_count: 5 } },
    })

    const err = await archiveClass('c1').catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).code).toBe('class_has_students')
    expect(((err as ApiError).details as { student_count: number }).student_count).toBe(5)

    mock.onPost('/admin/classes/c1/archive').replyOnce(200, { ...CLASS_A, archived_at: '2026-10-02T08:00:00Z' })
    expect((await archiveClass('c1')).archived_at).toBe('2026-10-02T08:00:00Z')
  })
})
