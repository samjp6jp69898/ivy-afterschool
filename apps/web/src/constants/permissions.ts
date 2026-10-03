// FRONTEND-008：後台權限碼（家長端不使用）。與後端 apps/api/app/core/permissions.py 的 Permission enum
// 同步，由 permissions.consistency.spec.ts 檢查一致性。
// 新增權限碼時：後端 enum、docs/domain_spec.md §3、本檔同時修改。
// `*` 是 admin 角色在 DB 的萬用值，不是權限碼。

export const PERMISSIONS = {
  DASHBOARD_READ: 'dashboard:read',
  STAFF_READ: 'staff:read',
  STAFF_WRITE: 'staff:write',
  ROLES_READ: 'roles:read',
  ROLES_WRITE: 'roles:write',
  SETTINGS_READ: 'settings:read',
  SETTINGS_WRITE: 'settings:write',
  AUDIT_READ: 'audit:read',
  CLASSES_READ: 'classes:read',
  CLASSES_WRITE: 'classes:write',
  STUDENTS_READ: 'students:read',
  STUDENTS_WRITE: 'students:write',
  STUDENTS_SENSITIVE: 'students:sensitive',
  GUARDIANS_WRITE: 'guardians:write',
  ATTENDANCE_READ: 'attendance:read',
  ATTENDANCE_OPERATE: 'attendance:operate',
  ATTENDANCE_AMEND: 'attendance:amend',
  LEAVES_READ: 'leaves:read',
  LEAVES_WRITE: 'leaves:write',
  HOMEWORK_READ: 'homework:read',
  HOMEWORK_WRITE: 'homework:write',
  PICKUP_READ: 'pickup:read',
  PICKUP_OPERATE: 'pickup:operate',
  PICKUP_OVERRIDE: 'pickup:override',
  EXAMS_READ: 'exams:read',
  EXAMS_WRITE: 'exams:write',
  EXAMS_PUBLISH: 'exams:publish',
  STUDENTS_PURGE: 'students:purge',
} as const

export type PermissionCode = (typeof PERMISSIONS)[keyof typeof PERMISSIONS]

export const ALL_PERMISSION_CODES: readonly PermissionCode[] = Object.freeze(Object.values(PERMISSIONS))

const CODE_SET: ReadonlySet<string> = new Set(ALL_PERMISSION_CODES)

export function isPermissionCode(v: string): v is PermissionCode {
  return CODE_SET.has(v)
}
