import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PublicConfig } from '../api/config'
import {
  LiffNotConfiguredError,
  _resetLiffForTests,
  clearLiffTokenRefreshMarker,
  forceLiffReloginOnce,
  idTokenNeedsRefresh,
  initLiff,
  type LiffClient,
} from './liff'

const sdk = vi.hoisted(() => ({
  init: vi.fn<(config: { liffId: string; withLoginOnExternalBrowser: boolean }) => Promise<void>>(),
  isLoggedIn: vi.fn<() => boolean>(),
  isInClient: vi.fn<() => boolean>(),
  login: vi.fn<(opts: { redirectUri: string }) => void>(),
  logout: vi.fn<() => void>(),
  getIDToken: vi.fn<() => string | null>(),
  getDecodedIDToken: vi.fn<() => { exp?: number } | null>(),
}))
const config = vi.hoisted(() => ({ getPublicConfig: vi.fn<() => Promise<PublicConfig>>() }))

vi.mock('@line/liff', () => ({ default: sdk }))
vi.mock('../api/config', () => config)

const LIMITS = {
  leave_past_days: 30,
  leave_future_days: 60,
  leave_max_attachments: 3,
  leave_max_attachment_mb: 10,
  authorization_max_days_ahead: 14,
  persons_max: 10,
}

function configWith(liffId: string): PublicConfig {
  return {
    liff_id: liffId,
    org_name: '快樂安親班',
    org_phone: '02-2345-6789',
    logo_url: null,
    add_friend_url: null,
    limits: LIMITS,
  }
}

type MockWindow = Window & { __PARENT_LIFF_MOCK__?: LiffClient }

const REDIRECT = 'https://x/parent/#/login'

describe('liff', () => {
  beforeEach(() => {
    _resetLiffForTests()
    vi.clearAllMocks()
    sessionStorage.clear()
    sdk.init.mockResolvedValue(undefined)
    sdk.isLoggedIn.mockReturnValue(true)
    config.getPublicConfig.mockResolvedValue(configWith('1650000000-abc'))
  })

  afterEach(() => {
    delete (window as MockWindow).__PARENT_LIFF_MOCK__
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
    sessionStorage.clear()
    _resetLiffForTests()
  })

  it('liff init uses liff id from config', async () => {
    const [first, second] = await Promise.all([initLiff(), initLiff()])
    const third = await initLiff()

    expect(sdk.init).toHaveBeenCalledTimes(1)
    expect(sdk.init.mock.calls[0]![0]).toEqual({ liffId: '1650000000-abc', withLoginOnExternalBrowser: true })
    expect(first).toBe(sdk)
    expect(second).toBe(sdk)
    expect(third).toBe(sdk)
  })

  it('liff init rejects when not configured', async () => {
    config.getPublicConfig.mockResolvedValue(configWith(''))

    const err = await initLiff().catch((e: unknown) => e)

    expect(err).toBeInstanceOf(LiffNotConfiguredError)
    expect((err as Error).message).toBe('安親班尚未設定 LINE 登入，請聯絡安親班')
    expect(sdk.init).toHaveBeenCalledTimes(0)
  })

  it('liff init can retry after failure', async () => {
    sdk.init.mockRejectedValueOnce(new Error('net'))

    const err = await initLiff().catch((e: unknown) => e)
    expect((err as Error).message).toBe('net')

    await expect(initLiff()).resolves.toBe(sdk)
    expect(sdk.init).toHaveBeenCalledTimes(2)
  })

  it('liff init retries after config failure', async () => {
    config.getPublicConfig.mockRejectedValueOnce(new Error('config down'))

    await expect(initLiff()).rejects.toThrow('config down')
    await expect(initLiff()).resolves.toBe(sdk)
    expect(config.getPublicConfig).toHaveBeenCalledTimes(2)
  })

  it('liff idTokenNeedsRefresh boundary', () => {
    expect(idTokenNeedsRefresh({ exp: 1000 }, 939)).toBe(false)
    expect(idTokenNeedsRefresh({ exp: 1000 }, 941)).toBe(true)
    expect(idTokenNeedsRefresh(null, 0)).toBe(true)
    expect(idTokenNeedsRefresh(undefined, 0)).toBe(true)
    expect(idTokenNeedsRefresh({}, 0)).toBe(true)
  })

  it('liff forceLiffReloginOnce only once', async () => {
    await initLiff()

    expect(forceLiffReloginOnce({ redirectUri: REDIRECT, nowMs: 1 })).toBe(true)
    expect(sdk.logout).toHaveBeenCalledTimes(1)
    expect(sdk.login).toHaveBeenCalledTimes(1)
    expect(sdk.login).toHaveBeenCalledWith({ redirectUri: REDIRECT })
    expect(sessionStorage.getItem('parent_liff_relogin_marker')).toBe('1')

    expect(forceLiffReloginOnce({ redirectUri: REDIRECT, nowMs: 2 })).toBe(false)
    expect(sdk.login).toHaveBeenCalledTimes(1)

    clearLiffTokenRefreshMarker()
    expect(sessionStorage.getItem('parent_liff_relogin_marker')).toBeNull()
    expect(forceLiffReloginOnce({ redirectUri: REDIRECT, nowMs: 3 })).toBe(true)
    expect(sdk.login).toHaveBeenCalledTimes(2)
  })

  it('liff forceLiffReloginOnce skips logout when not logged in and needs init', async () => {
    expect(forceLiffReloginOnce({ redirectUri: REDIRECT, nowMs: 1 })).toBe(false)
    expect(sdk.login).toHaveBeenCalledTimes(0)
    expect(sessionStorage.getItem('parent_liff_relogin_marker')).toBeNull()

    await initLiff()
    sdk.isLoggedIn.mockReturnValue(false)

    expect(forceLiffReloginOnce({ redirectUri: REDIRECT, nowMs: 1 })).toBe(true)
    expect(sdk.logout).toHaveBeenCalledTimes(0)
    expect(sdk.login).toHaveBeenCalledTimes(1)
  })

  it('liff forceLiffReloginOnce still tries when sessionStorage unavailable', async () => {
    await initLiff()
    vi.stubGlobal('sessionStorage', {
      getItem: () => {
        throw new Error('SecurityError')
      },
      setItem: () => {
        throw new Error('SecurityError')
      },
      removeItem: () => {
        throw new Error('SecurityError')
      },
    })

    expect(forceLiffReloginOnce({ redirectUri: REDIRECT, nowMs: 1 })).toBe(true)
    expect(sdk.login).toHaveBeenCalledTimes(1)
    expect(() => clearLiffTokenRefreshMarker()).not.toThrow()
  })

  it('liff e2e mock only outside production', async () => {
    const fake: LiffClient = {
      isLoggedIn: () => true,
      isInClient: () => true,
      login: vi.fn(),
      logout: vi.fn(),
      getIDToken: () => 'e2e-id-token',
      getDecodedIDToken: () => ({ exp: 9999999999 }),
    }
    ;(window as MockWindow).__PARENT_LIFF_MOCK__ = fake

    vi.stubEnv('PROD', false)
    await expect(initLiff()).resolves.toBe(fake)
    expect(sdk.init).toHaveBeenCalledTimes(0)
    expect(forceLiffReloginOnce({ redirectUri: REDIRECT, nowMs: 1 })).toBe(true)
    expect(fake.login).toHaveBeenCalledWith({ redirectUri: REDIRECT })

    _resetLiffForTests()
    vi.stubEnv('PROD', true)
    await expect(initLiff()).resolves.toBe(sdk)
    expect(sdk.init).toHaveBeenCalledTimes(1)
  })
})
