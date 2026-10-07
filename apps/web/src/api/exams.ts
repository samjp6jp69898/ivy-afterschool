// FRONTEND-230：考試成績 api client（BACKEND-467~478，docs/tasks/api_index.md §11）。
// 分數（後端 Decimal）一律以 JSON number 傳輸。錯誤由 adminHttp 轉成 ApiError 往上拋：
// 422 invalid_score_cells（details 為 InvalidScoreCell[]）/ exam_has_no_subjects、
// 409 exam_published / full_score_below_existing / exam_already_published / exam_not_published。
import type { ExamStatus, ISODate, ISODateTime, Page } from '@/shared/types/api'
import { adminHttp } from './http'

export interface ExamTypeBrief {
  id: string
  name: string
}

export interface ExamClassBrief {
  id: string
  name: string
}

export interface ExamSubject {
  subject_id: string
  subject_name: string
  full_score: number
  sort_order: number
}

export interface Exam {
  id: string
  name: string
  exam_type: ExamTypeBrief
  exam_date: ISODate
  grade_level: number | null
  class: ExamClassBrief | null
  status: ExamStatus
  published_at: ISODateTime | null
  published_by_name: string | null
  note: string | null
  subjects: ExamSubject[]
  roster_count: number
  created_at: ISODateTime
}

export interface ExamListParams {
  q?: string
  status?: ExamStatus
  exam_type_id?: string
  class_id?: string
  grade_level?: number
  date_from?: ISODate
  date_to?: ISODate
  page?: number
  page_size?: number
}

/** grade_level 與 class_id 至少一個 */
export interface ExamCreate {
  name: string
  exam_type_id: string
  exam_date: ISODate
  grade_level?: number | null
  class_id?: string | null
  note?: string | null
}

/** 已發布只可改 name / exam_date / note，否則 409 exam_published */
export type ExamUpdate = Partial<ExamCreate>

export interface ExamSubjectInput {
  subject_id: string
  full_score: number
  sort_order: number
}

export interface ScoreCell {
  student_id: string
  subject_id: string
  score: number | null
  is_absent: boolean
  note: string | null
  updated_at: ISODateTime
}

export interface ScoreGridStudent {
  id: string
  student_no: string
  name: string
  class_name: string | null
  /** 是否在考試範圍內（不在範圍的學生仍可能有舊成績） */
  in_roster: boolean
}

export interface ScoreGrid {
  exam: Exam
  students: ScoreGridStudent[]
  subjects: ExamSubject[]
  cells: ScoreCell[]
}

export interface ScoreCellInput {
  student_id: string
  subject_id: string
  score: number | null
  is_absent: boolean
  note?: string | null
}

export interface ScoresPutBody {
  /** 每次只送有變動的格子，1~2000 格 */
  cells: ScoreCellInput[]
  /** 已發布考試修改時重新通知變動學生的家長 */
  notify_parents: boolean
}

export interface ScoresPutResult {
  written: number
  changed: number
  renotified_students: number
}

/** 422 invalid_score_cells 的 details 項目 */
export interface InvalidScoreCell {
  student_id: string
  subject_id: string
  code: string
}

export interface SubjectSummary {
  subject_id: string
  subject_name: string
  full_score: number
  scored_count: number
  absent_count: number
  missing_count: number
  average: number | null
  max: number | null
  min: number | null
}

export interface ExamSummary {
  exam_id: string
  roster_count: number
  subjects: SubjectSummary[]
}

export interface ExamHistoryScore {
  subject_id: string
  subject_name: string
  full_score: number
  score: number | null
  is_absent: boolean
}

export interface ExamHistoryItem {
  exam_id: string
  exam_name: string
  exam_type_name: string
  exam_date: ISODate
  status: ExamStatus
  scores: ExamHistoryScore[]
}

const examPath = (id: string) => `/admin/exams/${encodeURIComponent(id)}`

export async function listExams(params: ExamListParams = {}): Promise<Page<Exam>> {
  const res = await adminHttp.get<Page<Exam>>('/admin/exams', { params })
  return res.data
}

export async function createExam(body: ExamCreate): Promise<Exam> {
  const res = await adminHttp.post<Exam>('/admin/exams', body)
  return res.data
}

export async function getExam(id: string): Promise<Exam> {
  const res = await adminHttp.get<Exam>(examPath(id))
  return res.data
}

export async function updateExam(id: string, body: ExamUpdate): Promise<Exam> {
  const res = await adminHttp.patch<Exam>(examPath(id), body)
  return res.data
}

/** 只限 draft */
export async function deleteExam(id: string): Promise<void> {
  await adminHttp.delete(examPath(id))
}

/** 整批覆蓋科目；移除的科目會連同該科成績一起刪除 */
export async function setExamSubjects(id: string, items: ExamSubjectInput[]): Promise<Exam> {
  const res = await adminHttp.put<Exam>(`${examPath(id)}/subjects`, { items })
  return res.data
}

export async function fetchScoreGrid(id: string): Promise<ScoreGrid> {
  const res = await adminHttp.get<ScoreGrid>(`${examPath(id)}/scores`)
  return res.data
}

export async function saveScores(id: string, body: ScoresPutBody): Promise<ScoresPutResult> {
  const res = await adminHttp.put<ScoresPutResult>(`${examPath(id)}/scores`, body)
  return res.data
}

export async function publishExam(id: string): Promise<Exam> {
  const res = await adminHttp.post<Exam>(`${examPath(id)}/publish`)
  return res.data
}

export async function unpublishExam(id: string): Promise<Exam> {
  const res = await adminHttp.post<Exam>(`${examPath(id)}/unpublish`)
  return res.data
}

export async function fetchExamSummary(id: string): Promise<ExamSummary> {
  const res = await adminHttp.get<ExamSummary>(`${examPath(id)}/summary`)
  return res.data
}

export async function fetchStudentExamHistory(studentId: string): Promise<ExamHistoryItem[]> {
  const res = await adminHttp.get<ExamHistoryItem[]>(`/admin/students/${encodeURIComponent(studentId)}/exam-history`)
  return res.data
}
