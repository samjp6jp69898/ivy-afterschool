import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError, type Page } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { type ParentExamDetail, type ParentExamSummary, getChildExam, listChildExams } from './exams'
import { parentHttp } from './http'

const MIDTERM: ParentExamSummary = {
  exam_id: 'e1',
  name: '第一次段考',
  exam_type_name: '段考',
  exam_date: '2026-10-05',
  published_at: '2026-10-06T02:00:00Z',
  subject_count: 3,
}

const QUIZ: ParentExamSummary = {
  exam_id: 'e2',
  name: '數學小考',
  exam_type_name: '小考',
  exam_date: '2026-09-21',
  published_at: '2026-09-22T09:30:00Z',
  subject_count: 1,
}

const DETAIL: ParentExamDetail = {
  exam_id: 'e1',
  name: '第一次段考',
  exam_type_name: '段考',
  exam_date: '2026-10-05',
  note: '範圍：第一至三課',
  subjects: [
    { subject_name: '國語', full_score: 100, score: 92.5, is_absent: false, note: null },
    { subject_name: '數學', full_score: 100, score: null, is_absent: true, note: '請病假' },
    { subject_name: '英語', full_score: 50, score: null, is_absent: false, note: null },
  ],
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

describe('examsApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(parentHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('examsApi lists exams with paging', async () => {
    const page: Page<ParentExamSummary> = { items: [MIDTERM], total: 1 }
    mock.onGet('/parent/children/s1/exams', { params: { page: 1, page_size: 20 } }).reply(200, page)

    const exams = await listChildExams('s1')

    expect(exams).toEqual(page)
    expect(mock.history.get).toHaveLength(1)
    expect(mock.history.get[0]!.url).toBe('/parent/children/s1/exams')
    expect(mock.history.get[0]!.params).toEqual({ page: 1, page_size: 20 })
  })

  it('examsApi list passes explicit paging', async () => {
    const page: Page<ParentExamSummary> = { items: [QUIZ], total: 51 }
    mock.onGet('/parent/children/s2/exams', { params: { page: 2, page_size: 50 } }).reply(200, page)

    const exams = await listChildExams('s2', 2, 50)

    expect(exams.items).toEqual([QUIZ])
    expect(exams.total).toBe(51)
    expect(mock.history.get[0]!.url).toBe('/parent/children/s2/exams')
    expect(mock.history.get[0]!.params).toEqual({ page: 2, page_size: 50 })
  })

  it('examsApi list propagates 422', async () => {
    const details = [
      { loc: ['query', 'page'], msg: 'Input should be greater than or equal to 1', type: 'greater_than_equal' },
    ]
    mock.onGet('/parent/children/s1/exams').reply(422, {
      error: { code: 'validation_error', message: '輸入資料格式錯誤', details },
    })

    const err = await apiErrorOf(listChildExams('s1', 0))

    expect(err.status).toBe(422)
    expect(err.code).toBe('validation_error')
    expect(err.details).toEqual(details)
    expect(mock.history.get[0]!.params).toEqual({ page: 0, page_size: 20 })
  })

  it('examsApi detail path and 404', async () => {
    mock
      .onGet('/parent/children/s1/exams/e1')
      .reply(200, DETAIL)
      .onGet('/parent/children/s1/exams/e9')
      .reply(404, { error: { code: 'exam_not_found', message: '找不到這次考試', details: null } })

    const detail = await getChildExam('s1', 'e1')
    const err = await apiErrorOf(getChildExam('s1', 'e9'))

    expect(detail).toEqual(DETAIL)
    expect(mock.history.get.map((r) => r.url)).toEqual([
      '/parent/children/s1/exams/e1',
      '/parent/children/s1/exams/e9',
    ])
    expect(err.status).toBe(404)
    expect(err.code).toBe('exam_not_found')
  })

  it('examsApi detail keeps numeric scores and absent subjects as returned', async () => {
    mock.onGet('/parent/children/s1/exams/e1').reply(200, DETAIL)

    const detail = await getChildExam('s1', 'e1')

    expect(detail.subjects[0]!.score).toBe(92.5)
    expect(detail.subjects[1]).toEqual({
      subject_name: '數學',
      full_score: 100,
      score: null,
      is_absent: true,
      note: '請病假',
    })
    expect(detail.subjects[2]!.score).toBeNull()
    expect(detail.subjects[2]!.is_absent).toBe(false)
  })
})
