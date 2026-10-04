// FRONTEND-054：稽核紀錄查詢 api client（BACKEND-105）。
import type { Page } from '@/shared/types/api'
import { adminHttp } from './http'

export type AuditActorType = 'staff' | 'parent' | 'system' | 'device'

export interface AuditLog {
  id: string
  created_at: string
  actor_type: AuditActorType
  actor_id: string | null
  actor_name: string | null
  action: string
  entity_type: string | null
  entity_id: string | null
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  ip: string | null
  user_agent: string | null
}

export interface AuditLogQuery {
  action?: string
  action_prefix?: string
  entity_type?: string
  entity_id?: string
  actor_type?: AuditActorType
  actor_id?: string
  /** YYYY-MM-DD */
  date_from?: string
  date_to?: string
  page?: number
  page_size?: number
}

export async function listAuditLogs(q: AuditLogQuery = {}): Promise<Page<AuditLog>> {
  const res = await adminHttp.get<Page<AuditLog>>('/admin/audit-logs', { params: q })
  return res.data
}
