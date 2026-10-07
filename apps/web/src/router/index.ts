// FRONTEND-026：後台 router。移植 ivy FE:src/router/index.ts 的 `router.beforeEach(authGuard)` 寫法；
// 去掉 route loading bar、chunk self-heal、Sentry。
// 不建立 module-level 的 router 單例：createWebHashHistory() 一建立就對 window 掛 popstate 監聽，
// 多一個沒掛到 app 的 router 會在上一頁 / 下一頁時重複執行 authGuard（多打一次 /auth/me）。
// bootstrap（FRONTEND-028）與測試都經 createAdminRouter 建立，測試傳 memory history。
import { createRouter, createWebHashHistory, type Router, type RouterHistory } from 'vue-router'
import { authGuard } from '@/router/authGuard'
import { routes } from '@/router/routes'

export const APP_TITLE = '安親班管理系統'

export function createAdminRouter(history: RouterHistory = createWebHashHistory()): Router {
  const router = createRouter({ history, routes })

  router.beforeEach((to) => authGuard(to))

  router.afterEach((to, _from, failure) => {
    // 被中止的導航畫面沒換，標題也不換；被導向的導航不會進到這裡，落點那次才會
    if (failure) return
    const title = to.meta.title
    document.title = title ? `${title} - ${APP_TITLE}` : APP_TITLE
  })

  return router
}
