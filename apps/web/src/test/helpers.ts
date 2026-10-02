// INFRA-013：元件測試共用的掛載與 API mock 輔助。
// FRONTEND / PARENT 的元件測試一律用 mountWithApp 掛 Pinia + Router，用 createApiMock 攔 axios。
import { createTestingPinia, type TestingPinia } from '@pinia/testing'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import type { AxiosInstance } from 'axios'
import MockAdapter from 'axios-mock-adapter'
import { vi } from 'vitest'
import type { Component, ComponentPublicInstance } from 'vue'
import { createMemoryHistory, createRouter, type RouteRecordRaw, type Router } from 'vue-router'

export interface MountWithAppOptions<P extends Record<string, unknown> = Record<string, unknown>> {
  props?: P
  /** 預設一條 catch-all 路由，讓 useRoute / RouterLink 不需要真實路由表也能運作 */
  routes?: RouteRecordRaw[]
  /** 預設 '/' */
  initialRoute?: string
  /** 以 store id 為 key 的初始 state，例如 { auth: { user: {...} } } */
  piniaInitialState?: Record<string, unknown>
  /** 預設 false：store action 真的執行（仍被 vi.fn 包起來，可斷言呼叫） */
  stubActions?: boolean
  slots?: Record<string, string>
}

export interface MountedApp {
  wrapper: VueWrapper<ComponentPublicInstance>
  router: Router
  pinia: TestingPinia
}

const FALLBACK_ROUTES: RouteRecordRaw[] = [
  { path: '/:pathMatch(.*)*', component: { render: () => null } },
]

/**
 * 掛載元件並注入 memory-history Router 與 testing Pinia，等到路由就緒、promise 清空後回傳。
 * Element Plus 元件由 vitest 沿用 vite 設定中的 unplugin 自動解析，不需全域安裝。
 */
export async function mountWithApp<P extends Record<string, unknown> = Record<string, unknown>>(
  component: Component,
  options: MountWithAppOptions<P> = {},
): Promise<MountedApp> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: options.routes ?? FALLBACK_ROUTES,
  })
  await router.push(options.initialRoute ?? '/')
  await router.isReady()

  const pinia = createTestingPinia({
    initialState: options.piniaInitialState ?? {},
    stubActions: options.stubActions ?? false,
    createSpy: vi.fn,
  })

  const wrapper = mount(component, {
    props: options.props,
    slots: options.slots,
    global: { plugins: [router, pinia] },
  })
  await flushPromises()

  return { wrapper, router, pinia }
}

/**
 * 以 axios-mock-adapter 攔截指定 axios instance；未註冊的請求直接丟錯（不是靜默 404），
 * 讓漏 mock 的 API 呼叫在測試裡立刻現形。
 */
export function createApiMock(instance: AxiosInstance): MockAdapter {
  return new MockAdapter(instance, { onNoMatch: 'throwException' })
}
