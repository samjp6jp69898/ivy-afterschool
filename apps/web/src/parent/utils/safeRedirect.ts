/**
 * 登入 / 綁定成功後導回原頁、點通知 deep_link 時用的「安全站內相對路徑」驗證。
 *
 * `redirect` query 與通知的 `deep_link` 都是外部可控輸入（LINE 推播連結），
 * 在 `router.replace()` / `router.push()` 之前一律先驗證：只接受站內相對路徑，
 * 任何看起來會被瀏覽器解析成外站的輸入都視為不安全。
 *
 * 擋的項目：
 * - 絕對 URL（`https://evil.com`）、`javascript:` 偽協議、裸網域：不以 `/` 開頭
 * - 協議相對路徑（開頭 `//`）：瀏覽器會依目前協議補上，等同導去外站
 * - 開頭 `/\`：部分瀏覽器把 `\` 當 `/` 解析，是常見的 open-redirect 繞過手法
 * - 前導 / 尾隨空白、控制字元：瀏覽器解析 URL 時會移除 tab / 換行（`/\t/evil.com` → `//evil.com`）
 * - 登入頁 / 綁定頁本身：登入後又被導回登入頁會形成迴圈
 */

// 與 vue-router 預設匹配一致：不分大小寫、容許單一結尾斜線
const AUTH_LOOP_PATH = /^\/(login|bind)\/?$/i

function hasControlChar(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    const code = s.charCodeAt(i)
    if (code <= 0x1f || code === 0x7f) return true
  }
  return false
}

function pathPart(s: string): string {
  return s.split(/[?#]/, 1)[0] ?? ''
}

export function isSafeRedirectPath(raw: unknown): raw is string {
  if (typeof raw !== 'string' || raw.length === 0) return false
  if (raw.trim() !== raw) return false
  if (hasControlChar(raw)) return false
  if (!raw.startsWith('/')) return false
  if (raw.startsWith('//') || raw.startsWith('/\\')) return false
  if (AUTH_LOOP_PATH.test(pathPart(raw))) return false
  return true
}

/** 驗證失敗或缺值一律 fallback；預設 `/`（家長端首頁）。 */
export function resolveSafeRedirect(raw: unknown, fallback = '/'): string {
  return isSafeRedirectPath(raw) ? raw : fallback
}
