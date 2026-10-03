import { describe, expect, it } from 'vitest'
import {
  ATTENDANCE_STATUSES,
  AUTHORIZATION_EFFECTIVE_STATUSES,
  BINDING_STATUSES,
  CHECK_IN_SOURCES,
  CHECK_OUT_SOURCES,
  CLASS_STAFF_ROLES,
  EXAM_STATUSES,
  GENDERS,
  GUARDIAN_RELATIONS,
  HOMEWORK_ITEM_STATUSES,
  HOMEWORK_OVERALL_STATUSES,
  LEAVE_STATUSES,
  LEAVE_TYPES,
  NOTIFICATION_EVENTS,
  PICKUP_COMPLETION_METHODS,
  PICKUP_REQUEST_STATUSES,
  PICKUP_SOURCES,
  STUDENT_STATUSES,
} from '@/shared/types/api'
import {
  ATTENDANCE_STATUS_META,
  AUTHORIZATION_STATUS_META,
  BINDING_STATUS_META,
  CHECK_IN_SOURCE_LABELS,
  CHECK_OUT_SOURCE_LABELS,
  CLASS_STAFF_ROLE_LABELS,
  EXAM_STATUS_META,
  GENDER_LABELS,
  GUARDIAN_RELATION_LABELS,
  HOMEWORK_ITEM_STATUS_META,
  HOMEWORK_OVERALL_STATUS_META,
  LEAVE_STATUS_META,
  LEAVE_TYPE_META,
  NOTIFICATION_EVENT_LABELS,
  PICKUP_COMPLETION_METHOD_LABELS,
  PICKUP_REQUEST_STATUS_META,
  PICKUP_SOURCE_LABELS,
  STUDENT_STATUS_META,
  statusMeta,
} from './statusLabels'

const sortedKeys = (map: object) => Object.keys(map).sort()
const sorted = (values: readonly string[]) => [...values].sort()

describe('statusLabels', () => {
  it('statusLabels covers every enum value', () => {
    const pairs: [object, readonly string[]][] = [
      [ATTENDANCE_STATUS_META, ATTENDANCE_STATUSES],
      [HOMEWORK_ITEM_STATUS_META, HOMEWORK_ITEM_STATUSES],
      [HOMEWORK_OVERALL_STATUS_META, HOMEWORK_OVERALL_STATUSES],
      [PICKUP_REQUEST_STATUS_META, PICKUP_REQUEST_STATUSES],
      [AUTHORIZATION_STATUS_META, AUTHORIZATION_EFFECTIVE_STATUSES],
      [EXAM_STATUS_META, EXAM_STATUSES],
      [STUDENT_STATUS_META, STUDENT_STATUSES],
      [BINDING_STATUS_META, BINDING_STATUSES],
      [LEAVE_TYPE_META, LEAVE_TYPES],
      [NOTIFICATION_EVENT_LABELS, NOTIFICATION_EVENTS],
      [LEAVE_STATUS_META, LEAVE_STATUSES],
      [CHECK_IN_SOURCE_LABELS, CHECK_IN_SOURCES],
      [CHECK_OUT_SOURCE_LABELS, CHECK_OUT_SOURCES],
      [PICKUP_SOURCE_LABELS, PICKUP_SOURCES],
      [PICKUP_COMPLETION_METHOD_LABELS, PICKUP_COMPLETION_METHODS],
      [GENDER_LABELS, GENDERS],
      [GUARDIAN_RELATION_LABELS, GUARDIAN_RELATIONS],
      [CLASS_STAFF_ROLE_LABELS, CLASS_STAFF_ROLES],
    ]
    for (const [map, values] of pairs) {
      expect(sortedKeys(map)).toEqual(sorted(values))
      for (const v of Object.values(map) as unknown[]) {
        const label = typeof v === 'string' ? v : (v as { label: string }).label
        expect(label.length).toBeGreaterThan(0)
      }
    }
  })

  it('statusLabels representative values', () => {
    expect(ATTENDANCE_STATUS_META.absent).toEqual({ label: '缺席', tone: 'danger' })
    expect(ATTENDANCE_STATUS_META.expected).toEqual({ label: '預計到班', tone: 'warning' })
    expect(PICKUP_REQUEST_STATUS_META.arrived).toEqual({ label: '家長已到', tone: 'danger' })
    expect(PICKUP_REQUEST_STATUS_META.completed).toEqual({ label: '已接走', tone: 'success' })
    expect(HOMEWORK_ITEM_STATUS_META.correcting.label).toBe('訂正中')
    expect(HOMEWORK_OVERALL_STATUS_META.done).toEqual({ label: '已完成', tone: 'success' })
    expect(GUARDIAN_RELATION_LABELS.grandparent).toBe('祖父母')
    expect(AUTHORIZATION_STATUS_META.expired).toEqual({ label: '已過期', tone: 'info' })
    expect(BINDING_STATUS_META.code_issued).toEqual({ label: '已發綁定碼', tone: 'warning' })
    expect(LEAVE_TYPE_META.sick).toEqual({ label: '病假', tone: 'warning' })
    expect(CHECK_OUT_SOURCE_LABELS.pickup).toBe('接送完成')
    expect(PICKUP_COMPLETION_METHOD_LABELS.visual_match).toBe('目視核對')
    expect(NOTIFICATION_EVENT_LABELS['pickup.requested']).toBe('家長發起接送')
    expect(NOTIFICATION_EVENT_LABELS['homework.done']).toBe('作業完成')
  })

  it('statusLabels statusMeta falls back for unknown values', () => {
    expect(statusMeta(PICKUP_REQUEST_STATUS_META, 'pending')).toEqual({ label: '待回覆', tone: 'warning' })
    expect(statusMeta(PICKUP_REQUEST_STATUS_META, 'weird')).toEqual({ label: 'weird', tone: 'info' })
    expect(statusMeta(PICKUP_REQUEST_STATUS_META, null)).toEqual({ label: '—', tone: 'info' })
    expect(statusMeta(PICKUP_REQUEST_STATUS_META, undefined)).toEqual({ label: '—', tone: 'info' })
    // 原型鏈上的名稱不可被當成合法值
    expect(statusMeta(PICKUP_REQUEST_STATUS_META, 'toString')).toEqual({ label: 'toString', tone: 'info' })
  })
})
