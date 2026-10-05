import type MockAdapter from 'axios-mock-adapter'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { adminHttp } from '@/api/http'
import { createApiMock } from '@/test/helpers'
import { useLookupsStore } from './lookups'

function makeClass(id: string, name: string, academicYear: number, sortOrder = 0) {
  return {
    id,
    name,
    grade_levels: [1, 2],
    academic_year: academicYear,
    sort_order: sortOrder,
    archived_at: null,
    student_count: 10,
    staff: [],
  }
}

function getCount(mock: MockAdapter, url: string): number {
  return mock.history.get.filter((c) => c.url === url).length
}

describe('lookups store', () => {
  let mock: MockAdapter

  beforeEach(() => {
    setActivePinia(createPinia())
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('lookups store ensure loads once per kind', async () => {
    mock.onGet('/admin/subjects').reply(200, [{ id: 's1', name: '國語', sort_order: 1, is_active: true }])
    const store = useLookupsStore()

    await Promise.all([store.ensure('subjects'), store.ensure('subjects')])
    await store.ensure('subjects')

    expect(getCount(mock, '/admin/subjects')).toBe(1)
    expect(mock.history.get[0]?.params).toEqual({ active_only: true })
    expect(store.subjectsLoaded).toBe(true)
    expect(store.subjectOptions).toEqual([{ label: '國語', value: 's1' }])
  })

  it('lookups store ensure loads several kinds with their queries', async () => {
    mock.onGet('/admin/classes').reply(200, [])
    mock.onGet('/admin/exam-types').reply(200, [{ id: 't1', name: '月考', sort_order: 1, is_active: true }])
    const store = useLookupsStore()

    await store.ensure('classes', 'examTypes')

    expect(mock.history.get.map((c) => [c.url, c.params])).toEqual([
      ['/admin/classes', { include_archived: false }],
      ['/admin/exam-types', { active_only: true }],
    ])
    expect(store.loaded).toEqual({ classes: true, subjects: false, examTypes: true, schools: false })
    expect(store.examTypeOptions).toEqual([{ label: '月考', value: 't1' }])
  })

  it('lookups store invalidate forces reload', async () => {
    mock.onGet('/admin/schools').reply(200, [])
    const store = useLookupsStore()

    await store.ensure('schools')
    store.invalidate('schools')
    expect(store.schoolsLoaded).toBe(false)
    await store.ensure('schools')

    expect(getCount(mock, '/admin/schools')).toBe(2)
  })

  it('lookups store ignores a load invalidated in flight', async () => {
    mock.onGet('/admin/schools').replyOnce(200, [{ id: 'old', name: '舊資料國小', short_name: null, is_active: true }])
    mock.onGet('/admin/schools').replyOnce(200, [{ id: 'new', name: '新資料國小', short_name: null, is_active: true }])
    const store = useLookupsStore()

    const first = store.ensure('schools')
    store.invalidate('schools')
    const second = store.ensure('schools')
    await Promise.all([first, second])

    expect(getCount(mock, '/admin/schools')).toBe(2)
    expect(store.schools.map((s) => s.id)).toEqual(['new'])
    expect(store.schoolsLoaded).toBe(true)
  })

  it('lookups store classOptions filters by academic year', async () => {
    mock
      .onGet('/admin/classes')
      .reply(200, [makeClass('c1', '低年級A班', 115), makeClass('c2', '高年級B班', 114)])
    const store = useLookupsStore()

    await store.ensure('classes')

    expect(store.classOptions(115)).toEqual([{ label: '低年級A班', value: 'c1' }])
    expect(store.classOptions()).toHaveLength(2)
  })

  it('lookups store sorts classes by academic year desc then sort order', async () => {
    mock
      .onGet('/admin/classes')
      .reply(200, [makeClass('c1', '舊班', 114, 0), makeClass('c2', '乙班', 115, 2), makeClass('c3', '甲班', 115, 1)])
    const store = useLookupsStore()

    await store.ensure('classes')

    expect(store.classes.map((c) => c.id)).toEqual(['c3', 'c2', 'c1'])
  })

  it('lookups store schoolOptions prefers short name', async () => {
    mock.onGet('/admin/schools').reply(200, [
      { id: 'sc1', name: '某某國民小學', short_name: '某某國小', is_active: true },
      { id: 'sc2', name: '大同國小', short_name: null, is_active: true },
    ])
    const store = useLookupsStore()

    await store.ensure('schools')

    expect(store.schoolOptions.map((o) => o.label)).toEqual(['某某國小', '大同國小'])
    expect(store.schoolOptions.map((o) => o.value)).toEqual(['sc1', 'sc2'])
  })

  it('lookups store failure leaves kind unloaded', async () => {
    mock.onGet('/admin/exam-types').replyOnce(500, {
      error: { code: 'internal_error', message: '伺服器錯誤', details: null },
    })
    const store = useLookupsStore()

    await expect(store.ensure('examTypes')).rejects.toMatchObject({ code: 'internal_error' })
    expect(store.examTypesLoaded).toBe(false)

    mock.onGet('/admin/exam-types').replyOnce(200, [])
    await store.ensure('examTypes')

    expect(getCount(mock, '/admin/exam-types')).toBe(2)
    expect(store.examTypesLoaded).toBe(true)
  })
})
