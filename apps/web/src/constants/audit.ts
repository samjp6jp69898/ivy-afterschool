// FRONTEND-072 / 073：稽核紀錄頁與差異抽屜共用的對照表。
// 動作中文對照（AUDIT_ACTION_LABELS）只有列表頁用，放在 AuditLogView.vue。
import type { AuditActorType } from '@/api/auditLogs'

/** 已知 entity_type 的中文名稱；未知類型顯示原文 */
export const AUDIT_ENTITY_LABELS: Record<string, string> = {
  staff_user: '員工帳號',
  role: '角色',
  system_setting: '系統設定',
  student: '學生',
  student_import: '學生匯入',
  academic_year: '學年度',
  guardian: '監護人',
  student_attendance: '出勤',
  exam: '考試',
  exam_score: '成績',
  pickup_request: '接送請求',
  pickup_authorization: '代理接送授權',
}

export type AuditActorTagType = 'info' | 'success' | 'warning' | 'primary'

/** 操作者類型 tag：員工灰、家長綠、系統橘、裝置藍 */
export const AUDIT_ACTOR_TAGS: Record<AuditActorType, { label: string; type: AuditActorTagType }> = {
  staff: { label: '員工', type: 'info' },
  parent: { label: '家長', type: 'success' },
  system: { label: '系統', type: 'warning' },
  device: { label: '裝置', type: 'primary' },
}
