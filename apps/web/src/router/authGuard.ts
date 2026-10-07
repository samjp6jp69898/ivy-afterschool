// FRONTEND-025：後台路由守衛（docs/architecture_decisions.md §6：路由 meta.permission + authGuard 預設拒絕）。
// 移植 ivy FE:src/router/index.ts::authGuard 的 return-style guard（回傳 true 放行或路由目標）、
// 「/ 無權限時導到第一個有權限的頁」、「指名頁面無權限導 403 頁」；去掉 portal / teacher / platform 分支、
// Sentry 與 409 refresh race 特例。
// 每條路由恰屬 public / authOnly / permission 其一（FRONTEND-024）；三者都沒有的視為漏設，一律拒絕。
import {
  createRouterMatcher,
  START_LOCATION,
  type RouteLocationNormalized,
  type RouteLocationRaw,
} from 'vue-router'
import { routes } from '@/router/routes'
import { useAuthStore } from '@/stores/auth'

const LOGIN_PATH = '/login'
const CHANGE_PASSWORD_PATH = '/change-password'
const FORBIDDEN_PATH = '/forbidden'

// 只用來把 redirect 路徑解析回路由表（取 meta.permission），不做導航
const matcher = createRouterMatcher(routes, {})

/** 只接受站內絕對路徑：以 `/` 開頭、不以 `//` 或 `/\` 開頭、不含 `://`（防 open redirect） */
export function safeRedirect(raw: unknown): string | null {
  if (typeof raw !== 'string') return null
  if (!raw.startsWith('/') || raw.startsWith('//') || raw.startsWith('/\\') || raw.includes('://')) return null
  return raw
}

/** 依路由表順序找第一個持有 meta.permission 的頁，跳過帶參數（含 `:`）的路徑；都沒有回 null */
export function firstAllowedPath(perms: ReadonlySet<string>): string | null {
  for (const record of routes) {
    const permission = record.meta?.permission
    if (permission && !record.path.includes(':') && perms.has(permission)) return record.path
  }
  return null
}

function pathnameOf(fullPath: string): string {
  const end = fullPath.search(/[?#]/)
  return end === -1 ? fullPath : fullPath.slice(0, end)
}

/** 站內路徑是否解析到一條持有 meta.permission 的路由；public / authOnly / 漏設的目標都不算 */
function hasRoutePermission(fullPath: string, perms: ReadonlySet<string>): boolean {
  let permission: string | undefined
  try {
    permission = matcher.resolve({ path: pathnameOf(fullPath) }, START_LOCATION).meta.permission
  } catch {
    return false
  }
  return permission != null && perms.has(permission)
}

/**
 * 登入後（FRONTEND-047）與已登入再進 /login 的落點：
 * `safeRedirect(redirectRaw)` 且有該頁權限 → 該路徑；否則 `firstAllowedPath`；都沒有 → /forbidden。
 */
export function landingPath(redirectRaw: unknown, perms: ReadonlySet<string>): string {
  const redirect = safeRedirect(redirectRaw)
  if (redirect !== null && hasRoutePermission(redirect, perms)) return redirect
  return firstAllowedPath(perms) ?? FORBIDDEN_PATH
}

function forbidden(to: RouteLocationNormalized): RouteLocationRaw {
  return { path: FORBIDDEN_PATH, query: { from: to.fullPath } }
}

export async function authGuard(to: RouteLocationNormalized): Promise<true | RouteLocationRaw> {
  const auth = useAuthStore()
  await auth.restore()

  if (to.meta.public) {
    if (to.path !== LOGIN_PATH || !auth.isAuthenticated) return true
    if (auth.mustChangePassword) return CHANGE_PASSWORD_PATH
    return landingPath(to.query.redirect, auth.permissions)
  }

  if (!auth.isAuthenticated) {
    return to.path === '/' ? { path: LOGIN_PATH } : { path: LOGIN_PATH, query: { redirect: to.fullPath } }
  }

  if (auth.mustChangePassword && !to.meta.allowWhenMustChangePassword) return CHANGE_PASSWORD_PATH

  if (to.meta.authOnly) return true

  const required = to.meta.permission
  // 沒有 permission 也不是 public / authOnly：漏設 meta，預設拒絕而不是放行
  if (required == null) return forbidden(to)
  if (auth.permissions.has(required)) return true

  // `/` 是登入後的預設落點：導到第一個有權限的頁屬自動落地，不是錯誤
  if (to.path === '/') return firstAllowedPath(auth.permissions) ?? FORBIDDEN_PATH
  return forbidden(to)
}
