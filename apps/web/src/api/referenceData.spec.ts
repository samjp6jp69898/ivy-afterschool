import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createApiMock } from '@/test/helpers'
import { adminHttp } from './http'
import { closedDaysApi, examTypesApi, schoolsApi, subjectsApi } from './referenceData'

describe('referenceDataApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('referenceDataApi lists with query', async () => {
    mock
      .onGet('/admin/subjects', { params: { active_only: true } })
      .reply(200, [{ id: 's1', name: '國語', sort_order: 1, is_active: true }])
    const range = { date_from: '2026-10-01', date_to: '2026-10-31' }
    mock
      .onGet('/admin/closed-days', { params: range })
      .reply(200, [{ id: 'd1', date: '2026-10-10', reason: '國慶日' }])

    const subjects = await subjectsApi.list({ active_only: true })
    const closedDays = await closedDaysApi.list(range)

    expect(subjects[0]?.name).toBe('國語')
    expect(closedDays).toEqual([{ id: 'd1', date: '2026-10-10', reason: '國慶日' }])
    expect(mock.history.get.map((c) => [c.url, c.params])).toEqual([
      ['/admin/subjects', { active_only: true }],
      ['/admin/closed-days', range],
    ])
  })

  it('referenceDataApi creates and updates', async () => {
    mock
      .onPost('/admin/schools')
      .reply(201, { id: 'sc1', name: '某某國小', short_name: '某某', is_active: true })
    mock.onPatch('/admin/exam-types/t1').reply(200, { id: 't1', name: '月考', sort_order: 1, is_active: false })

    const school = await schoolsApi.create({ name: '某某國小', short_name: '某某' })
    const examType = await examTypesApi.update('t1', { is_active: false })

    expect(school.short_name).toBe('某某')
    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({ name: '某某國小', short_name: '某某' })
    expect(examType.is_active).toBe(false)
    expect(JSON.parse(mock.history.patch[0]?.data as string)).toEqual({ is_active: false })
  })

  it('referenceDataApi remove returns deactivated flag', async () => {
    mock.onDelete('/admin/subjects/s1').reply(200, { deleted: false, deactivated: true })

    const result = await subjectsApi.remove('s1')

    expect(result).toEqual({ deleted: false, deactivated: true })
    expect(mock.history.delete[0]?.url).toBe('/admin/subjects/s1')
  })
})
