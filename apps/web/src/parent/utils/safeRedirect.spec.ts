import { describe, expect, it } from 'vitest'
import { isSafeRedirectPath, resolveSafeRedirect } from './safeRedirect'

describe('safeRedirect', () => {
  it('safeRedirect accepts in-app path', () => {
    expect(resolveSafeRedirect('/pickup?child=abc')).toBe('/pickup?child=abc')
    expect(resolveSafeRedirect('/leaves/12#detail', '/more')).toBe('/leaves/12#detail')
    expect(isSafeRedirectPath('/')).toBe(true)
  })

  it('safeRedirect rejects absolute and protocol-relative url', () => {
    expect(resolveSafeRedirect('https://evil.com')).toBe('/')
    expect(resolveSafeRedirect('//evil.com')).toBe('/')
    expect(resolveSafeRedirect('/\\evil.com')).toBe('/')
    expect(resolveSafeRedirect('\\\\evil.com')).toBe('/')
    expect(resolveSafeRedirect('///evil.com')).toBe('/')
  })

  it('safeRedirect rejects javascript and bare host', () => {
    expect(resolveSafeRedirect('javascript:alert(1)')).toBe('/')
    expect(resolveSafeRedirect('JaVaScRiPt:alert(1)')).toBe('/')
    expect(resolveSafeRedirect('data:text/html,<script>alert(1)</script>')).toBe('/')
    expect(resolveSafeRedirect('evil.com/x')).toBe('/')
  })

  it('safeRedirect rejects whitespace and control chars', () => {
    expect(resolveSafeRedirect(' /pickup')).toBe('/')
    expect(resolveSafeRedirect('/pickup ')).toBe('/')
    expect(resolveSafeRedirect('/pickup\n')).toBe('/')
    expect(resolveSafeRedirect('/pi\u0000ckup')).toBe('/')
    expect(resolveSafeRedirect('/pi\u007fckup')).toBe('/')
    // 瀏覽器解析 URL 時會移除 tab / 換行，'/\t/evil.com' 會變成 '//evil.com'
    expect(resolveSafeRedirect('/\t/evil.com')).toBe('/')
    expect(resolveSafeRedirect('/\r\n/evil.com')).toBe('/')
    expect(resolveSafeRedirect(' /pickup')).toBe('/')
  })

  it('safeRedirect rejects login and bind loop', () => {
    expect(resolveSafeRedirect('/login?redirect=/x')).toBe('/')
    expect(resolveSafeRedirect('/bind')).toBe('/')
    expect(resolveSafeRedirect('/bind#step2')).toBe('/')
    expect(resolveSafeRedirect('/leaves', '/more')).toBe('/leaves')
    // vue-router 預設不分大小寫、容許單一結尾斜線，這些變體同樣會匹配到登入 / 綁定頁
    expect(resolveSafeRedirect('/LOGIN')).toBe('/')
    expect(resolveSafeRedirect('/login/')).toBe('/')
    expect(resolveSafeRedirect('/Bind/?x=1')).toBe('/')
    // 只擋登入 / 綁定頁本身，不擋前綴相同的其他路徑
    expect(resolveSafeRedirect('/login-help')).toBe('/login-help')
    expect(resolveSafeRedirect('/binding/list')).toBe('/binding/list')
    expect(resolveSafeRedirect('/more?from=/login')).toBe('/more?from=/login')
  })

  it('safeRedirect rejects non string', () => {
    expect(resolveSafeRedirect(['/a'])).toBe('/')
    expect(resolveSafeRedirect(undefined, '/more')).toBe('/more')
    expect(resolveSafeRedirect(null)).toBe('/')
    expect(resolveSafeRedirect(42)).toBe('/')
    expect(resolveSafeRedirect({ toString: () => '/a' })).toBe('/')
    expect(isSafeRedirectPath('')).toBe(false)
  })

  it('safeRedirect keeps same origin for encoded bypass payloads', () => {
    const base = 'https://afterschool.example/parent/'
    const payloads = [
      '/%2F%2Fevil.com',
      '/%5Cevil.com',
      '/%5C%5Cevil.com',
      '/%09/evil.com',
      '/%0a/evil.com',
      '/.%2F/evil.com',
      '/@evil.com',
      '/javascript:alert(1)',
      '//evil.com',
      '/\\evil.com',
      '/\t/evil.com',
      'https:evil.com',
    ]
    for (const payload of payloads) {
      const resolved = resolveSafeRedirect(payload)
      expect(new URL(resolved, base).origin, payload).toBe('https://afterschool.example')
    }
    // 編碼過的斜線不會被瀏覽器當成路徑分隔，原樣保留為站內路徑
    expect(resolveSafeRedirect('/%2F%2Fevil.com')).toBe('/%2F%2Fevil.com')
  })
})
