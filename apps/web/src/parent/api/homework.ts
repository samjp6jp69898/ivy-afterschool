// PARENT-070：家長端作業進度 api client（BACKEND-391）。
import type { HHMM, HomeworkItemStatus, HomeworkOverallStatus, ISODate, ISODateTime } from '@/shared/types/api'
import { parentHttp } from './http'

export interface HomeworkToday {
  student_id: string
  date: ISODate
  items: { title: string; subject_name: string | null; status: HomeworkItemStatus }[]
  overall_status: HomeworkOverallStatus
  ready_eta: HHMM | null
  note: string | null
  updated_at: ISODateTime | null
}

/** date 省略時不帶 query（後端以台北今日為準） */
export async function getChildHomework(childId: string, date?: ISODate): Promise<HomeworkToday> {
  const res = await parentHttp.get<HomeworkToday>(
    `/parent/children/${encodeURIComponent(childId)}/homework`,
    date === undefined ? undefined : { params: { date } },
  )
  return res.data
}
