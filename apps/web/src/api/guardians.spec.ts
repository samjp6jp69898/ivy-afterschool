import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import {
  createGuardian,
  deleteGuardian,
  issueBindingCode,
  listGuardians,
  unbindGuardian,
  updateGuardian,
} from './guardians'
import { adminHttp } from './http'

const FATHER = {
  id: 'g1',
  student_id: 's1',
  name: '王大明',
  relation: 'father',
  phone: '0912-000-111',
  is_primary: true,
  can_pickup: true,
  receives_notifications: true,
  binding: { status: 'bound', parent_display_name: '大明', code_expires_at: null },
}

describe('guardiansApi', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('guardiansApi lists and creates', async () => {
    const body = {
      name: '林美麗',
      relation: 'mother' as const,
      phone: '0912-000-123',
      is_primary: true,
      can_pickup: true,
      receives_notifications: true,
    }
    mock.onGet('/admin/students/s1/guardians').reply(200, [FATHER])
    mock.onPost('/admin/students/s1/guardians').reply(201, {
      ...body,
      id: 'g2',
      student_id: 's1',
      binding: { status: 'unbound', parent_display_name: null, code_expires_at: null },
    })

    const list = await listGuardians('s1')
    const created = await createGuardian('s1', body)

    expect(list[0]?.binding.status).toBe('bound')
    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual(body)
    expect(created.id).toBe('g2')
  })

  it('guardiansApi updates and deletes', async () => {
    mock.onPatch('/admin/guardians/g1').reply(200, { ...FATHER, can_pickup: false })
    mock.onDelete('/admin/guardians/g1').reply(204)

    const updated = await updateGuardian('g1', { can_pickup: false })
    const removed = await deleteGuardian('g1')

    expect(updated.can_pickup).toBe(false)
    expect(JSON.parse(mock.history.patch[0]?.data as string)).toEqual({ can_pickup: false })
    expect(removed).toBeUndefined()
    expect(mock.history.delete[0]?.url).toBe('/admin/guardians/g1')
  })

  it('guardiansApi issues binding code once', async () => {
    mock.onPost('/admin/guardians/g2/binding-code').reply(201, {
      guardian_id: 'g2',
      code: 'K7M2Q9XP',
      expires_at: '2026-10-09T08:00:00Z',
    })

    const issued = await issueBindingCode('g2')

    expect(issued).toEqual({ guardian_id: 'g2', code: 'K7M2Q9XP', expires_at: '2026-10-09T08:00:00Z' })
  })

  it('guardiansApi surfaces bound conflict and unbind', async () => {
    mock.onPost('/admin/guardians/g1/binding-code').reply(409, {
      error: { code: 'guardian_already_bound', message: '此監護人已綁定', details: null },
    })
    mock.onPost('/admin/guardians/g1/unbind').reply(200, {
      ...FATHER,
      binding: { status: 'unbound', parent_display_name: null, code_expires_at: null },
    })

    const err = await issueBindingCode('g1').catch((e: unknown) => e)
    const unbound = await unbindGuardian('g1')

    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).code).toBe('guardian_already_bound')
    expect(unbound.binding.status).toBe('unbound')
  })
})
