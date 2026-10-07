import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { adminHttp } from './http'
import {
  activateStaffUser,
  createStaffUser,
  deactivateStaffUser,
  getStaffUser,
  listStaffOptions,
  listStaffUsers,
  resetStaffPassword,
  updateStaffUser,
  type StaffUser,
} from './staffUsers'

const CLERK: StaffUser = {
  id: 'u1',
  username: 'clerk01',
  display_name: '林行政',
  phone: '0912-000-123',
  email: 'clerk01@example.com',
  role: { id: 'r-clerk', code: 'clerk', name: '行政' },
  extra_permissions: ['audit:read'],
  revoked_permissions: [],
  effective_permissions: ['audit:read', 'classes:read', 'students:read'],
  is_active: true,
  must_change_password: false,
  last_login_at: '2026-10-06T09:00:00Z',
  created_at: '2026-09-01T01:00:00Z',
}

const TUTOR: StaffUser = {
  ...CLERK,
  id: 'u2',
  username: 'tutor02',
  display_name: '陳老師',
  phone: null,
  email: null,
  role: { id: 'r-tutor', code: 'tutor', name: '課輔老師' },
  extra_permissions: [],
  effective_permissions: ['classes:read', 'students:read'],
  must_change_password: true,
  last_login_at: null,
}

function conflict(code: string, message: string, details: unknown = null) {
  return { error: { code, message, details } }
}

describe('staffUsersApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('staffUsersApi lists with filters', async () => {
    const params = { q: '林', is_active: true, page: 1, page_size: 20 }
    mock.onGet('/admin/staff-users', { params }).reply(200, { items: [CLERK], total: 1 })

    const page = await listStaffUsers(params)

    expect(page.total).toBe(1)
    expect(page.items[0]).toMatchObject({ id: 'u1', username: 'clerk01', display_name: '林行政' })
    expect(page.items[0]?.effective_permissions).toEqual(['audit:read', 'classes:read', 'students:read'])
    expect(mock.history.get[0]?.params).toEqual(params)

    mock.onGet('/admin/staff-users', { params: { role_id: 'r-tutor', is_active: false } }).reply(200, {
      items: [],
      total: 0,
    })
    expect(await listStaffUsers({ role_id: 'r-tutor', is_active: false })).toEqual({ items: [], total: 0 })
  })

  it('staffUsersApi gets and updates', async () => {
    mock.onGet('/admin/staff-users/u1').reply(200, CLERK)
    mock.onPatch('/admin/staff-users/u1').reply(200, { ...CLERK, display_name: '林小美', phone: null })

    const user = await getStaffUser('u1')
    const updated = await updateStaffUser('u1', { display_name: '林小美', phone: null, extra_permissions: ['audit:read'] })

    expect(user.role.code).toBe('clerk')
    expect(user.last_login_at).toBe('2026-10-06T09:00:00Z')
    expect(updated.display_name).toBe('林小美')
    expect(updated.phone).toBeNull()
    expect(JSON.parse(mock.history.patch[0]?.data as string)).toEqual({
      display_name: '林小美',
      phone: null,
      extra_permissions: ['audit:read'],
    })
  })

  it('staffUsersApi create returns temp password', async () => {
    const body = {
      username: 'tutor02',
      display_name: '陳老師',
      role_id: 'r-tutor',
      extra_permissions: [],
      revoked_permissions: [],
    }
    mock.onPost('/admin/staff-users').reply(201, { user: { ...TUTOR, id: 'u2' }, temp_password: 'Tmp-8f3K2q9x' })
    const consoleSpies = (['log', 'info', 'debug', 'warn', 'error'] as const).map((level) =>
      vi.spyOn(console, level).mockImplementation(() => undefined),
    )

    const created = await createStaffUser(body)

    expect(created.temp_password).toBe('Tmp-8f3K2q9x')
    expect(created.user.id).toBe('u2')
    expect(created.user.must_change_password).toBe(true)
    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual(body)
    // 臨時密碼只存在回應與呼叫端：不寫 log、不落 storage
    for (const spy of consoleSpies) expect(spy).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('staffUsersApi reset deactivate activate hit correct paths', async () => {
    mock.onPost('/admin/staff-users/u2/reset-password').reply(200, { temp_password: 'Tmp-a' })
    mock.onPost('/admin/staff-users/u2/deactivate').reply(200, { ...TUTOR, is_active: false })
    mock.onPost('/admin/staff-users/u2/activate').reply(200, {
      user: { ...TUTOR, is_active: true, must_change_password: true },
      temp_password: 'Tmp-b',
    })

    const reset = await resetStaffPassword('u2')
    const deactivated = await deactivateStaffUser('u2')
    const activated = await activateStaffUser('u2')

    expect(reset.temp_password).toBe('Tmp-a')
    expect(deactivated.is_active).toBe(false)
    expect(activated.temp_password).toBe('Tmp-b')
    expect(activated.user).toMatchObject({ is_active: true, must_change_password: true })
    expect(mock.history.post.map((c) => c.url)).toEqual([
      '/admin/staff-users/u2/reset-password',
      '/admin/staff-users/u2/deactivate',
      '/admin/staff-users/u2/activate',
    ])
    // 三支都沒有 body
    expect(mock.history.post.every((c) => c.data === undefined)).toBe(true)
  })

  it('staffUsersApi surfaces conflict codes', async () => {
    mock.onPost('/admin/staff-users').replyOnce(409, conflict('username_taken', '帳號已被使用'))
    mock.onPost('/admin/staff-users/u1/activate').replyOnce(409, conflict('staff_already_active', '帳號已是啟用狀態'))
    mock.onPost('/admin/staff-users/u1/deactivate').replyOnce(409, conflict('cannot_deactivate_self', '不可停用自己'))
    mock.onPost('/admin/staff-users/u1/reset-password').replyOnce(409, conflict('cannot_reset_self', '不可重設自己的密碼'))
    mock
      .onPost('/admin/staff-users')
      .replyOnce(403, conflict('cannot_grant_permissions', '不可授予自己沒有的權限', { permissions: ['staff:write'] }))
    mock.onGet('/admin/staff-users/zzz').replyOnce(404, conflict('staff_user_not_found', '找不到員工帳號'))

    const taken = await createStaffUser({
      username: 'clerk01',
      display_name: '林行政',
      role_id: 'r-clerk',
      extra_permissions: [],
      revoked_permissions: [],
    }).catch((e: unknown) => e)
    const alreadyActive = await activateStaffUser('u1').catch((e: unknown) => e)
    const self = await deactivateStaffUser('u1').catch((e: unknown) => e)
    const resetSelf = await resetStaffPassword('u1').catch((e: unknown) => e)
    const grant = await createStaffUser({
      username: 'director02',
      display_name: '王主任',
      role_id: 'r-director',
      extra_permissions: ['staff:write'],
      revoked_permissions: [],
    }).catch((e: unknown) => e)
    const missing = await getStaffUser('zzz').catch((e: unknown) => e)

    for (const err of [taken, alreadyActive, self, resetSelf, grant, missing]) expect(err).toBeInstanceOf(ApiError)
    expect((taken as ApiError).code).toBe('username_taken')
    expect((taken as ApiError).status).toBe(409)
    expect((alreadyActive as ApiError).code).toBe('staff_already_active')
    expect((self as ApiError).code).toBe('cannot_deactivate_self')
    expect((resetSelf as ApiError).code).toBe('cannot_reset_self')
    expect((grant as ApiError).status).toBe(403)
    expect((grant as ApiError).code).toBe('cannot_grant_permissions')
    expect(((grant as ApiError).details as { permissions: string[] }).permissions).toEqual(['staff:write'])
    expect((missing as ApiError).status).toBe(404)
    expect((missing as ApiError).code).toBe('staff_user_not_found')
  })

  it('staffUsersApi lists staff options', async () => {
    mock.onGet('/admin/staff-users/options').reply(200, [
      { id: 'u3', display_name: '陳老師' },
      { id: 'u4', display_name: '林老師' },
    ])

    const options = await listStaffOptions()

    expect(options).toHaveLength(2)
    expect(options[1]?.display_name).toBe('林老師')
    expect(mock.history.get[0]?.url).toBe('/admin/staff-users/options')
    expect(mock.history.get[0]?.params).toBeUndefined()
  })

  it('staffUsersApi encodes ids in paths', async () => {
    mock.onGet('/admin/staff-users/u%2F1').reply(200, CLERK)
    mock.onPost('/admin/staff-users/u%201/activate').reply(200, { user: TUTOR, temp_password: 'Tmp-c' })

    await getStaffUser('u/1')
    await activateStaffUser('u 1')

    expect(mock.history.get[0]?.url).toBe('/admin/staff-users/u%2F1')
    expect(mock.history.post[0]?.url).toBe('/admin/staff-users/u%201/activate')
  })
})
