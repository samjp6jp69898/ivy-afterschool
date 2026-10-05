import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { adminHttp } from './http'
import { createRole, deleteRole, fetchPermissionCatalog, listRoles, updateRole } from './roles'

const ROLE_BASE = {
  description: null,
  staff_count: 0,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
}

describe('rolesApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('rolesApi lists roles and catalog', async () => {
    mock.onGet('/admin/roles').reply(200, [
      {
        ...ROLE_BASE,
        id: 'r1',
        code: 'admin',
        name: '管理員',
        is_system: true,
        permissions: ['*'],
        effective_permissions: ['audit:read', 'dashboard:read'],
        staff_count: 1,
      },
    ])
    mock.onGet('/admin/permissions').reply(200, {
      groups: [{ key: 'dashboard', label: '儀表板', permissions: [{ code: 'dashboard:read', label: '首頁儀表板' }] }],
    })

    const roles = await listRoles()
    const catalog = await fetchPermissionCatalog()

    expect(roles[0]?.is_system).toBe(true)
    expect(roles[0]?.permissions).toEqual(['*'])
    expect(catalog.groups[0]?.permissions[0]?.code).toBe('dashboard:read')
  })

  it('rolesApi create update delete', async () => {
    const createBody = { code: 'senior_tutor', name: '資深老師', description: '', permissions: ['exams:publish'] }
    const created = {
      ...ROLE_BASE,
      id: 'r5',
      code: 'senior_tutor',
      name: '資深老師',
      description: '',
      is_system: false,
      permissions: ['exams:publish'],
      effective_permissions: ['exams:publish'],
    }
    mock.onPost('/admin/roles').reply(201, created)
    mock.onPatch('/admin/roles/r5').reply(200, { ...created, name: '資深課輔' })
    mock.onDelete('/admin/roles/r5').reply(204)

    const role = await createRole(createBody)
    const updated = await updateRole('r5', { name: '資深課輔' })
    const removed = await deleteRole('r5')

    expect(role.code).toBe('senior_tutor')
    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual(createBody)
    expect(updated.name).toBe('資深課輔')
    expect(JSON.parse(mock.history.patch[0]?.data as string)).toEqual({ name: '資深課輔' })
    expect(removed).toBeUndefined()
    expect(mock.history.delete[0]?.url).toBe('/admin/roles/r5')
  })

  it('rolesApi surfaces grant error details', async () => {
    mock.onPatch('/admin/roles/r5').reply(403, {
      error: {
        code: 'cannot_grant_permissions',
        message: '不可授出自己沒有的權限',
        details: { permissions: ['staff:write'] },
      },
    })

    const err = await updateRole('r5', { permissions: ['staff:write'] }).catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(403)
    expect((err as ApiError).code).toBe('cannot_grant_permissions')
    expect((err as ApiError).details).toEqual({ permissions: ['staff:write'] })
  })

  it('rolesApi surfaces role in use conflict', async () => {
    mock.onDelete('/admin/roles/r5').reply(409, {
      error: { code: 'role_in_use', message: '仍有員工使用此角色', details: null },
    })

    const err = await deleteRole('r5').catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).code).toBe('role_in_use')
  })
})
