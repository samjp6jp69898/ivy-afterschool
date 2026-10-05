// PARENT-008：LIFF SDK 初始化與 id_token 重新登入保護。
// 移植 ivy FE:src/parent/services/liff.ts 的 initLiff、idTokenNeedsRefresh、forceLiffReloginOnce；
// 去掉 tenant meta / VITE_LIFF_ID fallback、markLineClientFromSdk、reportClientEvent。
//
// id_token 只有 1 小時效期且 SDK 不會主動更新，過期 token 需走完整 OAuth（logout + login）才會換新；
// sessionStorage marker 確保同一輪只強制重登一次，避免無限 redirect。
import { getPublicConfig } from '../api/config'

export interface LiffClient {
  isLoggedIn(): boolean
  isInClient(): boolean
  login(opts: { redirectUri: string }): void
  logout(): void
  getIDToken(): string | null
  getDecodedIDToken(): { exp?: number } | null
}

export class LiffNotConfiguredError extends Error {
  constructor(message = '安親班尚未設定 LINE 登入，請聯絡安親班') {
    super(message)
    this.name = 'LiffNotConfiguredError'
  }
}

declare global {
  interface Window {
    /** e2e（PARENT-190）以 addInitScript 注入的替身；production build 一律忽略 */
    __PARENT_LIFF_MOCK__?: LiffClient
  }
}

const RELOGIN_MARKER = 'parent_liff_relogin_marker'
// 剩餘 < 60 秒視為需要更新，避免送出途中過期
const ID_TOKEN_REFRESH_BUFFER_SECONDS = 60

let initPromise: Promise<LiffClient> | null = null
let client: LiffClient | null = null

async function loadClient(): Promise<LiffClient> {
  if (!import.meta.env.PROD && window.__PARENT_LIFF_MOCK__) return window.__PARENT_LIFF_MOCK__
  const { liff_id: liffId } = await getPublicConfig()
  if (!liffId) throw new LiffNotConfiguredError()
  // 動態載入：只有登入流程需要 SDK，不進首屏 chunk
  const { default: liff } = await import('@line/liff')
  await liff.init({ liffId, withLoginOnExternalBrowser: true })
  return liff
}

export function initLiff(): Promise<LiffClient> {
  if (initPromise) return initPromise
  const pending = loadClient().then(
    (c) => {
      client = c
      return c
    },
    (error: unknown) => {
      // 失敗即清空，讓重試真的重試
      if (initPromise === pending) initPromise = null
      throw error
    },
  )
  initPromise = pending
  return pending
}

export function idTokenNeedsRefresh(payload: { exp?: number } | null | undefined, nowSec: number): boolean {
  if (!payload || typeof payload.exp !== 'number') return true
  return payload.exp - nowSec < ID_TOKEN_REFRESH_BUFFER_SECONDS
}

/** 同一輪只強制 logout + login 一次；已強制過或尚未 init 回 false 不動作 */
export function forceLiffReloginOnce(opts: { redirectUri: string; nowMs: number }): boolean {
  if (!client) return false
  try {
    if (sessionStorage.getItem(RELOGIN_MARKER)) return false
    sessionStorage.setItem(RELOGIN_MARKER, String(opts.nowMs))
  } catch {
    // sessionStorage 不可用（隱私模式）→ 不阻擋，仍嘗試一次
  }
  try {
    if (client.isLoggedIn()) client.logout()
  } catch {
    // logout 失敗不影響後續 redirect
  }
  client.login({ redirectUri: opts.redirectUri })
  return true
}

export function clearLiffTokenRefreshMarker(): void {
  try {
    sessionStorage.removeItem(RELOGIN_MARKER)
  } catch {
    // sessionStorage 不可用 → 忽略
  }
}

export function _resetLiffForTests(): void {
  initPromise = null
  client = null
}
