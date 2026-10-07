import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { createApiMock } from '@/test/helpers'
import { bind, liffLogin, logout } from './auth'
import type { ParentMe } from './children'
import { parentHttp, parentRefreshHttp } from './http'

const ME: ParentMe = {
  id: 'p1',
  display_name: '王媽媽',
  picture_url: null,
  phone: null,
  children: [
    {
      id: 's1',
      name: '王小明',
      grade_level: 3,
      class_name: '三年級班',
      school_name: '快樂國小',
      photo_url: null,
      status: 'active',
    },
  ],
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

describe('authApi', () => {
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

  /** 第 index 個 POST 實際送出的 JSON body */
  function postBody(index: number): unknown {
    return JSON.parse(mock.history.post[index]!.data as string)
  }

  it('authApi liffLogin posts id_token', async () => {
    mock
      .onPost('/parent/auth/liff-login')
      .reply(200, { status: 'needs_binding', parent: null, name_hint: '王媽媽' })

    const result = await liffLogin('tok123')

    expect(mock.history.post.map((r) => r.url)).toEqual(['/parent/auth/liff-login'])
    expect(postBody(0)).toEqual({ id_token: 'tok123' })
    expect(result).toEqual({ status: 'needs_binding', parent: null, name_hint: '王媽媽' })
    expect(result.name_hint).toBe('王媽媽')
  })

  it('authApi liffLogin returns parent when already bound', async () => {
    mock.onPost('/parent/auth/liff-login').reply(200, { status: 'ok', parent: ME, name_hint: null })

    const result = await liffLogin('tok456')

    expect(result.status).toBe('ok')
    expect(result.parent).toEqual(ME)
    expect(result.parent?.children[0]?.name).toBe('王小明')
    expect(result.name_hint).toBeNull()
  })

  it('authApi liffLogin 401 rejects without refresh', async () => {
    mock.onPost('/parent/auth/liff-login').reply(401, {
      error: { code: 'invalid_id_token', message: 'LINE 登入驗證失敗，請重新開啟', details: null },
    })

    const err = await apiErrorOf(liffLogin('expired-token'))

    expect(err.status).toBe(401)
    expect(err.code).toBe('invalid_id_token')
    // 登入端點的 401 不是 session 過期：不可觸發 refresh（路徑要在 parentHttp 的 noRefreshPaths 內）
    expect(refreshMock.history.post).toHaveLength(0)
    expect(mock.history.post).toHaveLength(1)
  })

  it('authApi bind posts code', async () => {
    mock.onPost('/parent/auth/bind').reply(400, {
      error: { code: 'binding_code_expired', message: '綁定碼已過期，請向安親班索取新的綁定碼', details: null },
    })

    const err = await apiErrorOf(bind('ABCD2345'))

    expect(mock.history.post.map((r) => r.url)).toEqual(['/parent/auth/bind'])
    expect(postBody(0)).toEqual({ code: 'ABCD2345' })
    expect(err.status).toBe(400)
    expect(err.code).toBe('binding_code_expired')
  })

  it('authApi bind returns parent with the latest children', async () => {
    mock.onPost('/parent/auth/bind').reply(200, { parent: ME })

    const result = await bind('ABCD2345')

    expect(result).toEqual({ parent: ME })
    expect(result.parent.children).toHaveLength(1)
  })

  it('authApi bind 401 rejects without refresh', async () => {
    mock.onPost('/parent/auth/bind').reply(401, {
      error: { code: 'unauthenticated', message: '請重新登入', details: null },
    })

    const err = await apiErrorOf(bind('ABCD2345'))

    expect(err.status).toBe(401)
    expect(err.code).toBe('unauthenticated')
    expect(refreshMock.history.post).toHaveLength(0)
    expect(mock.history.post).toHaveLength(1)
  })

  it('authApi logout posts to logout', async () => {
    mock.onPost('/parent/auth/logout').reply(200, { message: '已登出' })

    const result = await logout()

    expect(result).toBeUndefined()
    expect(mock.history.post.map((r) => r.url)).toEqual(['/parent/auth/logout'])
    expect(mock.history.post[0]!.data).toBeUndefined()
  })

  it('authApi logout propagates failures', async () => {
    mock.onPost('/parent/auth/logout').reply(500, {
      error: { code: 'internal_error', message: '伺服器發生錯誤', details: null },
    })

    const err = await apiErrorOf(logout())

    expect(err.status).toBe(500)
    expect(err.code).toBe('internal_error')
  })

  it('authApi logout 401 rejects without refresh', async () => {
    mock.onPost('/parent/auth/logout').reply(401, {
      error: { code: 'unauthenticated', message: '請重新登入', details: null },
    })

    const err = await apiErrorOf(logout())

    expect(err.status).toBe(401)
    // 登出時 refresh 一個已失效的 session 沒有意義，還會把登出變成登入迴圈
    expect(refreshMock.history.post).toHaveLength(0)
    expect(mock.history.post).toHaveLength(1)
  })
})
