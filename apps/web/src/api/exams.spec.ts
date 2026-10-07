import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import {
  createExam,
  deleteExam,
  fetchExamSummary,
  fetchScoreGrid,
  fetchStudentExamHistory,
  getExam,
  listExams,
  publishExam,
  saveScores,
  setExamSubjects,
  unpublishExam,
  updateExam,
  type Exam,
  type InvalidScoreCell,
} from './exams'
import { adminHttp } from './http'

const EXAM: Exam = {
  id: 'e1',
  name: '第一次段考',
  exam_type: { id: 't1', name: '段考' },
  exam_date: '2026-10-15',
  grade_level: 3,
  class: null,
  status: 'draft',
  published_at: null,
  published_by_name: null,
  note: null,
  subjects: [{ subject_id: 'sub1', subject_name: '國語', full_score: 100, sort_order: 1 }],
  roster_count: 12,
  created_at: '2026-10-07T08:00:00Z',
}

const PUBLISHED: Exam = {
  ...EXAM,
  status: 'published',
  published_at: '2026-10-16T02:00:00Z',
  published_by_name: '林行政',
}

function conflict(code: string, message: string) {
  return { error: { code, message, details: null } }
}

describe('examsApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('examsApi lists and creates exam', async () => {
    const params = { status: 'draft' as const, page: 1, page_size: 20 }
    mock.onGet('/admin/exams', { params }).reply(200, { items: [EXAM], total: 1 })
    const body = { name: '第一次段考', exam_type_id: 't1', exam_date: '2026-10-15', grade_level: 3 }
    mock.onPost('/admin/exams').reply(201, EXAM)

    const page = await listExams(params)
    const created = await createExam(body)

    expect(page.items[0]?.name).toBe('第一次段考')
    expect(page.items[0]?.subjects[0]?.subject_name).toBe('國語')
    expect(page.total).toBe(1)
    expect(mock.history.get[0]?.params).toEqual(params)
    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual(body)
    expect(created.id).toBe('e1')
    expect(created.class).toBeNull()
  })

  it('examsApi lists with full filter set', async () => {
    const params = {
      q: '段考',
      status: 'published' as const,
      exam_type_id: 't1',
      class_id: 'c1',
      grade_level: 3,
      date_from: '2026-09-01',
      date_to: '2026-12-31',
      page: 2,
      page_size: 50,
    }
    mock.onGet('/admin/exams', { params }).reply(200, { items: [], total: 0 })

    const page = await listExams(params)

    expect(page).toEqual({ items: [], total: 0 })
    expect(mock.history.get[0]?.params).toEqual(params)
  })

  it('examsApi gets updates and deletes', async () => {
    mock.onGet('/admin/exams/e1').reply(200, EXAM)
    mock.onPatch('/admin/exams/e1').replyOnce(200, { ...EXAM, name: '期中考', note: '範圍第 1~3 課' })
    mock.onDelete('/admin/exams/e1').reply(204)

    expect((await getExam('e1')).roster_count).toBe(12)
    const updated = await updateExam('e1', { name: '期中考', note: '範圍第 1~3 課' })
    expect(updated.name).toBe('期中考')
    expect(JSON.parse(mock.history.patch[0]?.data as string)).toEqual({ name: '期中考', note: '範圍第 1~3 課' })
    expect(await deleteExam('e1')).toBeUndefined()
    expect(mock.history.delete[0]?.url).toBe('/admin/exams/e1')

    // 已發布只可改 name / exam_date / note
    mock.onPatch('/admin/exams/e1').replyOnce(409, conflict('exam_published', '考試已發布，不可修改範圍'))
    const err = await updateExam('e1', { class_id: 'c2' }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(409)
    expect((err as ApiError).code).toBe('exam_published')
  })

  it('examsApi fetches score grid', async () => {
    mock.onGet('/admin/exams/e1/scores').reply(200, {
      exam: EXAM,
      students: [
        { id: 's1', student_no: 'A001', name: '王小明', class_name: '三年甲班', in_roster: true },
        { id: 's2', student_no: 'A002', name: '林小華', class_name: null, in_roster: false },
      ],
      subjects: EXAM.subjects,
      cells: [
        { student_id: 's1', subject_id: 'sub1', score: 95.5, is_absent: false, note: null, updated_at: '2026-10-16T01:00:00Z' },
        { student_id: 's2', subject_id: 'sub1', score: null, is_absent: true, note: '請假', updated_at: '2026-10-16T01:00:00Z' },
      ],
    })

    const grid = await fetchScoreGrid('e1')

    expect(grid.exam.id).toBe('e1')
    expect(grid.students.map((s) => s.in_roster)).toEqual([true, false])
    expect(grid.cells[0]?.score).toBe(95.5)
    expect(grid.cells[1]).toMatchObject({ score: null, is_absent: true, note: '請假' })
    expect(mock.history.get[0]?.url).toBe('/admin/exams/e1/scores')
  })

  it('examsApi saves score cells with notify flag', async () => {
    const body = {
      cells: [
        { student_id: 's1', subject_id: 'sub1', score: 95, is_absent: false },
        { student_id: 's2', subject_id: 'sub1', score: null, is_absent: true },
      ],
      notify_parents: false,
    }
    mock.onPut('/admin/exams/e1/scores').reply(200, { written: 2, changed: 2, renotified_students: 0 })

    const result = await saveScores('e1', body)

    expect(result.changed).toBe(2)
    expect(result).toEqual({ written: 2, changed: 2, renotified_students: 0 })
    expect(JSON.parse(mock.history.put[0]?.data as string)).toEqual(body)
    expect(mock.history.put[0]?.url).toBe('/admin/exams/e1/scores')
  })

  it('examsApi surfaces invalid cells details', async () => {
    mock.onPut('/admin/exams/e1/scores').reply(422, {
      error: {
        code: 'invalid_score_cells',
        message: '部分分數不正確',
        details: [{ student_id: 's1', subject_id: 'sub1', code: 'score_out_of_range' }],
      },
    })

    const err = await saveScores('e1', {
      cells: [{ student_id: 's1', subject_id: 'sub1', score: 120, is_absent: false }],
      notify_parents: true,
    }).catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(422)
    expect((err as ApiError).code).toBe('invalid_score_cells')
    expect((err as ApiError).message).toBe('部分分數不正確')
    const details = (err as ApiError).details as InvalidScoreCell[]
    expect(details[0]?.code).toBe('score_out_of_range')
    expect(details[0]).toEqual({ student_id: 's1', subject_id: 'sub1', code: 'score_out_of_range' })
    expect(JSON.parse(mock.history.put[0]?.data as string).notify_parents).toBe(true)
  })

  it('examsApi subjects publish and history', async () => {
    const items = [{ subject_id: 'sub1', full_score: 100, sort_order: 1 }]
    mock.onPut('/admin/exams/e1/subjects').reply(200, EXAM)
    mock.onPost('/admin/exams/e1/publish').reply(200, PUBLISHED)
    mock.onPost('/admin/exams/e1/unpublish').reply(200, EXAM)
    mock.onGet('/admin/students/s1/exam-history').reply(200, [
      {
        exam_id: 'e1',
        exam_name: '第一次段考',
        exam_type_name: '段考',
        exam_date: '2026-10-15',
        status: 'published',
        scores: [{ subject_id: 'sub1', subject_name: '國語', full_score: 100, score: 95, is_absent: false }],
      },
    ])

    const withSubjects = await setExamSubjects('e1', items)
    const published = await publishExam('e1')
    const unpublished = await unpublishExam('e1')
    const history = await fetchStudentExamHistory('s1')

    expect(JSON.parse(mock.history.put[0]?.data as string)).toEqual({ items })
    expect(withSubjects.subjects).toHaveLength(1)
    expect(published.status).toBe('published')
    expect(published.published_by_name).toBe('林行政')
    expect(unpublished.status).toBe('draft')
    expect(mock.history.post.map((c) => c.url)).toEqual(['/admin/exams/e1/publish', '/admin/exams/e1/unpublish'])
    expect(history).toHaveLength(1)
    expect(Array.isArray(history[0]?.scores)).toBe(true)
    expect(history[0]?.scores[0]).toMatchObject({ subject_name: '國語', score: 95, is_absent: false })
    expect(mock.history.get[0]?.url).toBe('/admin/students/s1/exam-history')
  })

  it('examsApi fetches summary', async () => {
    mock.onGet('/admin/exams/e1/summary').reply(200, {
      exam_id: 'e1',
      roster_count: 12,
      subjects: [
        {
          subject_id: 'sub1',
          subject_name: '國語',
          full_score: 100,
          scored_count: 10,
          absent_count: 1,
          missing_count: 1,
          average: 86.5,
          max: 98,
          min: 62,
        },
      ],
    })

    const summary = await fetchExamSummary('e1')

    expect(summary.roster_count).toBe(12)
    expect(summary.subjects[0]).toMatchObject({ average: 86.5, max: 98, min: 62, missing_count: 1 })
  })

  it('examsApi surfaces publish and subject conflicts', async () => {
    mock.onPost('/admin/exams/e1/publish').replyOnce(422, {
      error: { code: 'exam_has_no_subjects', message: '尚未設定科目', details: null },
    })
    mock.onPost('/admin/exams/e1/publish').replyOnce(409, conflict('exam_already_published', '考試已發布'))
    mock.onPost('/admin/exams/e1/unpublish').replyOnce(409, conflict('exam_not_published', '考試尚未發布'))
    mock.onPut('/admin/exams/e1/subjects').replyOnce(409, conflict('full_score_below_existing', '滿分低於已登記分數'))

    const noSubjects = await publishExam('e1').catch((e: unknown) => e)
    const already = await publishExam('e1').catch((e: unknown) => e)
    const notPublished = await unpublishExam('e1').catch((e: unknown) => e)
    const belowExisting = await setExamSubjects('e1', [{ subject_id: 'sub1', full_score: 50, sort_order: 1 }]).catch(
      (e: unknown) => e,
    )

    for (const err of [noSubjects, already, notPublished, belowExisting]) expect(err).toBeInstanceOf(ApiError)
    expect([noSubjects, already, notPublished, belowExisting].map((e) => (e as ApiError).code)).toEqual([
      'exam_has_no_subjects',
      'exam_already_published',
      'exam_not_published',
      'full_score_below_existing',
    ])
    expect((noSubjects as ApiError).status).toBe(422)
    expect((already as ApiError).status).toBe(409)
    expect((belowExisting as ApiError).status).toBe(409)
  })

  it('examsApi encodes ids in paths', async () => {
    mock.onGet('/admin/exams/e%2F1').reply(200, EXAM)
    mock.onGet('/admin/students/s%201/exam-history').reply(200, [])

    await getExam('e/1')
    await fetchStudentExamHistory('s 1')

    expect(mock.history.get.map((c) => c.url)).toEqual(['/admin/exams/e%2F1', '/admin/students/s%201/exam-history'])
  })
})
