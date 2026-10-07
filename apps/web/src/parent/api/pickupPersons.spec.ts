import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { parentHttp, parentRefreshHttp } from './http'
import { type PickupPerson, createPickupPerson, deletePickupPerson, listPickupPersons } from './pickupPersons'

const UNAUTHORIZED = { error: { code: 'unauthenticated', message: '請重新登入', details: null } }

const AUNT: PickupPerson = {
  id: 'pp1',
  student_id: 's1',
  name: '李阿姨',
  relation: '保母',
  phone: '0912000123',
  photo_url: 'https://files.example.test/pickup-persons/pp1.jpg?sig=abc',
  created_at: '2026-10-07T01:00:00Z',
}

const UNCLE: PickupPerson = {
  id: 'pp2',
  student_id: 's1',
  name: '王大哥',
  relation: '叔叔',
  phone: '0912-000-456',
  photo_url: null,
  created_at: '2026-10-07T02:00:00Z',
}

async function apiErrorOf(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError)
    return e as ApiError
  }
  throw new Error('預期 reject 但 resolve 了')
}

describe('pickupPersonsApi', () => {
  let mock: MockAdapter
  let refreshMock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(parentHttp)
    refreshMock = createApiMock(parentRefreshHttp)
  })

  afterEach(() => {
    mock.restore()
    refreshMock.restore()
  })

  function newPhoto(): File {
    return new File(['x'], 'a.jpg', { type: 'image/jpeg' })
  }

  it('pickupPersonsApi list path', async () => {
    mock.onGet('/parent/children/s1/pickup-persons').reply(200, [AUNT, UNCLE])

    const persons = await listPickupPersons('s1')

    expect(persons).toEqual([AUNT, UNCLE])
    expect(persons[1]!.photo_url).toBeNull()
    expect(mock.history.get.map((r) => r.url)).toEqual(['/parent/children/s1/pickup-persons'])
  })

  it('pickupPersonsApi create sends multipart with photo', async () => {
    mock.onPost('/parent/children/s1/pickup-persons').reply(201, AUNT)

    const created = await createPickupPerson('s1', {
      name: '李阿姨',
      relation: '保母',
      phone: '0912000123',
      photo: newPhoto(),
    })

    const request = mock.history.post[0]!
    expect(request.url).toBe('/parent/children/s1/pickup-persons')
    // 不是 JSON 字串：parentHttp 預設的 application/json 會讓 axios 把 FormData 轉成 JSON、檔案遺失
    expect(request.data).toBeInstanceOf(FormData)
    expect(String(request.headers?.['Content-Type'])).not.toContain('application/json')
    const form = request.data as FormData
    expect(form.get('name')).toBe('李阿姨')
    expect(form.get('relation')).toBe('保母')
    expect(form.get('phone')).toBe('0912000123')
    const photo = form.get('photo') as File
    expect(photo.name).toBe('a.jpg')
    expect(photo.type).toBe('image/jpeg')
    expect(Array.from(form.keys()).sort()).toEqual(['name', 'phone', 'photo', 'relation'])
    expect(created).toEqual(AUNT)
  })

  it('pickupPersonsApi create without photo omits field', async () => {
    mock.onPost('/parent/children/s1/pickup-persons').reply(201, UNCLE)

    await createPickupPerson('s1', { name: '王大哥', relation: '叔叔', phone: '0912-000-456', photo: null })
    await createPickupPerson('s1', { name: '王大哥', relation: '叔叔', phone: '0912-000-456' })

    for (const request of mock.history.post) {
      expect(request.data).toBeInstanceOf(FormData)
      const form = request.data as FormData
      expect(form.has('photo')).toBe(false)
      expect(form.get('name')).toBe('王大哥')
      expect(form.get('relation')).toBe('叔叔')
      expect(form.get('phone')).toBe('0912-000-456')
    }
    expect(mock.history.post).toHaveLength(2)
  })

  it('pickupPersonsApi create resends multipart after session refresh', async () => {
    mock
      .onPost('/parent/children/s1/pickup-persons')
      .replyOnce(401, UNAUTHORIZED)
      .onPost('/parent/children/s1/pickup-persons')
      .replyOnce(201, AUNT)
    refreshMock.onPost('/parent/auth/refresh').reply(200, { parent: {} })

    const created = await createPickupPerson('s1', {
      name: '李阿姨',
      relation: '保母',
      phone: '0912000123',
      photo: newPhoto(),
    })

    expect(created).toEqual(AUNT)
    expect(refreshMock.history.post).toHaveLength(1)
    expect(mock.history.post).toHaveLength(2)
    for (const request of mock.history.post) {
      expect(request.data).toBeInstanceOf(FormData)
      const form = request.data as FormData
      expect(form.get('name')).toBe('李阿姨')
      expect((form.get('photo') as File).name).toBe('a.jpg')
    }
  })

  it('pickupPersonsApi limit error', async () => {
    mock.onPost('/parent/children/s1/pickup-persons').reply(409, {
      error: { code: 'pickup_person_limit_reached', message: '常用接送人已達上限', details: null },
    })

    const err = await apiErrorOf(
      createPickupPerson('s1', { name: '王大哥', relation: '叔叔', phone: '0912-000-456' }),
    )

    expect(err.status).toBe(409)
    expect(err.code).toBe('pickup_person_limit_reached')
    expect(err.message).toBe('常用接送人已達上限')
  })

  it('pickupPersonsApi create surfaces validation details', async () => {
    const details = [{ loc: ['body', 'phone'], msg: 'String should match pattern', type: 'string_pattern_mismatch' }]
    mock.onPost('/parent/children/s1/pickup-persons').reply(422, {
      error: { code: 'validation_error', message: '輸入資料格式錯誤', details },
    })

    const err = await apiErrorOf(createPickupPerson('s1', { name: '王大哥', relation: '叔叔', phone: 'abc' }))

    expect(err.status).toBe(422)
    expect(err.code).toBe('validation_error')
    expect(err.details).toEqual(details)
  })

  it('pickupPersonsApi delete path', async () => {
    mock.onDelete('/parent/pickup-persons/pp1').reply(204)

    const result = await deletePickupPerson('pp1')

    expect(result).toBeUndefined()
    expect(mock.history.delete.map((r) => r.url)).toEqual(['/parent/pickup-persons/pp1'])
  })

  it('pickupPersonsApi delete propagates 404', async () => {
    mock.onDelete('/parent/pickup-persons/pp9').reply(404, {
      error: { code: 'pickup_person_not_found', message: '找不到這位接送人', details: null },
    })

    const err = await apiErrorOf(deletePickupPerson('pp9'))

    expect(err.status).toBe(404)
    expect(err.code).toBe('pickup_person_not_found')
  })
})
