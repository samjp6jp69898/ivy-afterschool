// PARENT-150：家長端成績 api client（BACKEND-479 / 480）。
// 型別逐欄對照 apps/api/app/schemas/exams.py 的 ParentExamListItemOut / ParentExamDetailOut；
// 分數（Decimal）後端以 JSON number 輸出。後端只回已發布且該生有成績的考試，其餘一律 404 `exam_not_found`。
import type { ISODate, ISODateTime, Page } from '@/shared/types/api'
import { parentHttp } from './http'

export interface ParentExamSummary {
  exam_id: string
  name: string
  exam_type_name: string
  exam_date: ISODate
  published_at: ISODateTime
  subject_count: number
}

export interface ParentExamDetail {
  exam_id: string
  name: string
  exam_type_name: string
  exam_date: ISODate
  note: string | null
  subjects: {
    subject_name: string
    full_score: number
    /** 缺考或尚未登記時為 null */
    score: number | null
    is_absent: boolean
    note: string | null
  }[]
}

const childExamsPath = (childId: string) => `/parent/children/${encodeURIComponent(childId)}/exams`

/** 依後端順序（考試日期新到舊）；page ≥ 1、1 ≤ pageSize ≤ 200 由後端驗證 */
export async function listChildExams(
  childId: string,
  page = 1,
  pageSize = 20,
): Promise<Page<ParentExamSummary>> {
  const res = await parentHttp.get<Page<ParentExamSummary>>(childExamsPath(childId), {
    params: { page, page_size: pageSize },
  })
  return res.data
}

export async function getChildExam(childId: string, examId: string): Promise<ParentExamDetail> {
  const res = await parentHttp.get<ParentExamDetail>(`${childExamsPath(childId)}/${encodeURIComponent(examId)}`)
  return res.data
}
