import { describe, expect, it } from 'vitest'
import {
  ACTOR_CREATED_BY_TYPES,
  ApiError,
  ATTENDANCE_STATUSES,
  AUTHORIZATION_EFFECTIVE_STATUSES,
  AUTHORIZATION_STATUSES,
  BINDING_STATUSES,
  CHECK_IN_SOURCES,
  CHECK_OUT_SOURCES,
  CLASS_STAFF_ROLES,
  EXAM_STATUSES,
  GENDERS,
  GUARDIAN_RELATIONS,
  HOMEWORK_ITEM_STATUSES,
  HOMEWORK_OVERALL_STATUSES,
  isApiError,
  LEAVE_STATUSES,
  LEAVE_TYPES,
  NOTIFICATION_EVENTS,
  PICKUP_COMPLETION_METHODS,
  PICKUP_OPEN_STATUSES,
  PICKUP_REPLY_SOURCES,
  PICKUP_REQUEST_STATUSES,
  PICKUP_SOURCES,
  PICKUP_TERMINAL_STATUSES,
  STUDENT_STATUSES,
  VERIFICATION_METHODS,
} from './api'

describe('shared api types', () => {
  it('shared api types enum values match domain_spec', () => {
    expect(ATTENDANCE_STATUSES).toEqual(['expected', 'present', 'left', 'absent', 'leave'])
    expect(PICKUP_REQUEST_STATUSES).toEqual([
      'pending',
      'acknowledged',
      'arrived',
      'completed',
      'cancelled',
      'expired',
    ])
    expect(HOMEWORK_ITEM_STATUSES).toEqual(['todo', 'doing', 'correcting', 'done'])
    expect(NOTIFICATION_EVENTS).toHaveLength(13)
    expect(NOTIFICATION_EVENTS).toContain('pickup.arrived')
    expect(NOTIFICATION_EVENTS).toContain('binding.completed')

    // 其餘 enum 也逐一對照 domain_spec §1（值與順序）
    expect(CHECK_IN_SOURCES).toEqual(['manual', 'nfc'])
    expect(CHECK_OUT_SOURCES).toEqual(['manual', 'pickup', 'nfc'])
    expect(LEAVE_TYPES).toEqual(['sick', 'personal', 'other'])
    expect(LEAVE_STATUSES).toEqual(['active', 'cancelled'])
    expect(ACTOR_CREATED_BY_TYPES).toEqual(['parent', 'staff'])
    expect(HOMEWORK_OVERALL_STATUSES).toEqual(['not_started', 'in_progress', 'done'])
    expect(PICKUP_OPEN_STATUSES).toEqual(['pending', 'acknowledged', 'arrived'])
    expect(PICKUP_TERMINAL_STATUSES).toEqual(['completed', 'cancelled', 'expired'])
    expect(PICKUP_SOURCES).toEqual(['parent', 'staff', 'proxy'])
    expect(PICKUP_REPLY_SOURCES).toEqual(['auto', 'staff'])
    expect(PICKUP_COMPLETION_METHODS).toEqual(['guardian', 'code', 'visual_match', 'override'])
    expect(AUTHORIZATION_STATUSES).toEqual(['active', 'completed', 'cancelled'])
    expect(AUTHORIZATION_EFFECTIVE_STATUSES).toEqual(['active', 'completed', 'cancelled', 'expired'])
    expect(VERIFICATION_METHODS).toEqual(['code', 'visual_match', 'override'])
    expect(EXAM_STATUSES).toEqual(['draft', 'published'])
    expect(STUDENT_STATUSES).toEqual(['active', 'suspended', 'withdrawn'])
    expect(GENDERS).toEqual(['male', 'female', 'other'])
    expect(GUARDIAN_RELATIONS).toEqual(['father', 'mother', 'grandparent', 'other'])
    expect(BINDING_STATUSES).toEqual(['bound', 'code_issued', 'unbound'])
    expect(CLASS_STAFF_ROLES).toEqual(['lead', 'assistant'])
    expect(NOTIFICATION_EVENTS).toEqual([
      'attendance.checked_in',
      'attendance.checked_out',
      'leave.created',
      'leave.cancelled',
      'homework.eta_updated',
      'homework.done',
      'pickup.requested',
      'pickup.replied',
      'pickup.arrived',
      'pickup.completed',
      'pickup.cancelled',
      'exam.published',
      'binding.completed',
    ])
  })

  it('shared api types open and terminal pickup statuses partition all statuses', () => {
    expect([...PICKUP_OPEN_STATUSES, ...PICKUP_TERMINAL_STATUSES].sort()).toEqual(
      [...PICKUP_REQUEST_STATUSES].sort(),
    )
  })

  it('shared api types ApiError carries fields', () => {
    const err = new ApiError(409, 'leave_overlap', '請假期間重疊', { leave_id: 'l1' })

    expect(err.status).toBe(409)
    expect(err.code).toBe('leave_overlap')
    expect(err.message).toBe('請假期間重疊')
    expect(err.details).toEqual({ leave_id: 'l1' })
    expect(err.name).toBe('ApiError')
    expect(err instanceof Error).toBe(true)
  })

  it('shared api types ApiError details defaults to null', () => {
    expect(new ApiError(400, 'x', 'y').details).toBeNull()
  })

  it('shared api types isApiError discriminates', () => {
    expect(isApiError(new ApiError(400, 'x', 'y'))).toBe(true)
    expect(isApiError(new Error('y'))).toBe(false)
    expect(isApiError({ status: 400, code: 'x' })).toBe(false)
  })
})
