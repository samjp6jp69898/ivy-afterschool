// PARENT-007：家長端認證 api client（BACKEND-053 / 057 / 061）。
// refresh 由 PARENT-004 的 parentHttp 攔截器自動處理，這裡不提供；
// 三個端點都列在 parentHttp 的 noRefreshPaths，401 直接 reject 由呼叫端（登入頁 / 綁定頁）處理。
import type { ParentMe } from './children'
import { parentHttp } from './http'

export interface LiffLoginResult {
  status: 'ok' | 'needs_binding'
  /** needs_binding 時為 null：瀏覽器已持有 parent_bind cookie，等待輸入綁定碼 */
  parent: ParentMe | null
  /** needs_binding 時為 LINE 暱稱，供綁定頁問候；ok 時為 null */
  name_hint: string | null
}

export async function liffLogin(idToken: string): Promise<LiffLoginResult> {
  const res = await parentHttp.post<LiffLoginResult>('/parent/auth/liff-login', { id_token: idToken })
  return res.data
}

/** 首次綁定與加綁同一個端點；回傳的 parent.children 已含最新小孩清單 */
export async function bind(code: string): Promise<{ parent: ParentMe }> {
  const res = await parentHttp.post<{ parent: ParentMe }>('/parent/auth/bind', { code })
  return res.data
}

export async function logout(): Promise<void> {
  await parentHttp.post('/parent/auth/logout')
}
