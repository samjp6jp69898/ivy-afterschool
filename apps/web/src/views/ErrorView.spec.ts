import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { StaffMe } from '@/api/auth'
import { VIEW_LOADERS } from '@/router/viewLoaders'
import { mountWithApp } from '@/test/helpers'
import ErrorView from './ErrorView.vue'
import errorViewSource from './ErrorView.vue?raw'

const FORBIDDEN_DESC = '你的帳號沒有檢視此頁面的權限，如需使用請聯絡主任或管理員。'
const NOT_FOUND_DESC = '網址可能有誤或頁面已移除。'

function makeUser(permissions: string[]): StaffMe {
  return {
    id: 'u1',
    username: 'tutor01',
    display_name: '陳老師',
    role: { id: 'r1', code: 'tutor', name: '課輔老師' },
    permissions,
    must_change_password: false,
  }
}

type Session = { permissions: string[] } | 'anonymous'

async function mountError(kind: 'forbidden' | 'not_found', initialRoute: string, session: Session) {
  return mountWithApp(ErrorView, {
    props: { kind },
    initialRoute,
    routes: [
      { path: '/forbidden', component: ErrorView, props: { kind: 'forbidden' }, meta: { title: '沒有權限' } },
      { path: '/:pathMatch(.*)*', component: { render: () => null }, meta: { title: '頁面', layout: 'blank' } },
    ],
    piniaInitialState: {
      auth:
        session === 'anonymous'
          ? { user: null, status: 'anonymous' }
          : { user: makeUser(session.permissions), status: 'authenticated' },
    },
  })
}

function homeButton(wrapper: VueWrapper) {
  const button = wrapper.findAll('button').find((b) => b.text() === '回到首頁')
  if (!button) throw new Error('找不到「回到首頁」按鈕')
  return button
}

describe('ErrorView', () => {
  it('ErrorView forbidden shows from path', async () => {
    const { wrapper } = await mountError('forbidden', '/forbidden?from=/settings/roles', {
      permissions: ['homework:read'],
    })

    const text = wrapper.text()
    expect(text).toContain('沒有權限')
    expect(text).toContain(FORBIDDEN_DESC)
    expect(text).toContain('嘗試前往：/settings/roles')
    expect(wrapper.find('.error-view__from code').text()).toBe('/settings/roles')
    expect(wrapper.find('.error-view__icon').classes()).toContain('is-forbidden')
  })

  it('ErrorView forbidden without from hides the attempted path', async () => {
    const { wrapper } = await mountError('forbidden', '/forbidden', { permissions: ['homework:read'] })

    expect(wrapper.text()).toContain('沒有權限')
    expect(wrapper.text()).not.toContain('嘗試前往')
  })

  it('ErrorView not found copy', async () => {
    const { wrapper } = await mountError('not_found', '/no-such-page?from=/settings/roles', 'anonymous')

    const text = wrapper.text()
    expect(text).toContain('找不到頁面')
    expect(text).toContain(NOT_FOUND_DESC)
    expect(text).not.toContain('沒有權限')
    // from 只屬於 403
    expect(text).not.toContain('嘗試前往')
    expect(wrapper.find('.error-view__icon').classes()).toContain('is-not-found')
  })

  it('ErrorView home button routes by permission', async () => {
    const tutor = await mountError('forbidden', '/forbidden?from=/settings/roles', { permissions: ['homework:read'] })
    await homeButton(tutor.wrapper).trigger('click')
    await flushPromises()
    expect(tutor.router.currentRoute.value.path).toBe('/homework')

    const guest = await mountError('not_found', '/no-such-page', 'anonymous')
    await homeButton(guest.wrapper).trigger('click')
    await flushPromises()
    expect(guest.router.currentRoute.value.path).toBe('/login')

    const empty = await mountError('forbidden', '/forbidden', { permissions: [] })
    const button = homeButton(empty.wrapper)
    expect(button.attributes('disabled')).toBeDefined()
    expect(empty.wrapper.text()).toContain('你的帳號目前沒有可使用的頁面')
    await button.trigger('click')
    await flushPromises()
    expect(empty.router.currentRoute.value.path).toBe('/forbidden')
  })

  it('ErrorView home button for a signed-in user on the not found page', async () => {
    const { wrapper, router } = await mountError('not_found', '/no-such-page', {
      permissions: ['students:read', 'exams:read'],
    })

    expect(wrapper.text()).not.toContain('你的帳號目前沒有可使用的頁面')
    await homeButton(wrapper).trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/students')
  })

  it('ErrorView home button replaces the error page in history', async () => {
    const { wrapper, router } = await mountError('forbidden', '/students', { permissions: ['homework:read'] })
    await router.push('/forbidden?from=/settings/roles')
    await flushPromises()

    await homeButton(wrapper).trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/homework')

    router.back()
    await flushPromises()
    // replace：上一頁回到進錯誤頁之前的頁面，而不是錯誤頁本身
    expect(router.currentRoute.value.path).toBe('/students')
  })

  it('ErrorView fills the page only in the blank layout', async () => {
    const blank = await mountError('not_found', '/no-such-page', 'anonymous')
    expect(blank.wrapper.find('[data-test=error-view]').classes()).toContain('is-page')

    const admin = await mountError('forbidden', '/forbidden', { permissions: ['homework:read'] })
    expect(admin.wrapper.find('[data-test=error-view]').classes()).not.toContain('is-page')
  })

  it('ErrorView layout rules follow the approved mockup', () => {
    // happy-dom 不計算版面：72px 圓形 icon、標題 20px、按鈕最小寬 120 高 40、路徑可換行、整頁置中以原始碼斷言
    const style = errorViewSource.slice(errorViewSource.indexOf('<style'))
    expect(style).toMatch(/\.error-view__icon\s*\{[^}]*width:\s*72px;[^}]*height:\s*72px;[^}]*border-radius:\s*50%/)
    expect(style).toMatch(/\.error-view__title\s*\{[^}]*font-size:\s*20px/)
    expect(style).toMatch(/\.error-view__action\s+\.el-button\s*\{[^}]*min-width:\s*120px;[^}]*height:\s*40px/)
    expect(style).toMatch(/\.error-view__from\s*\{[^}]*word-break:\s*break-all/)
    expect(style).toMatch(/\.error-view\.is-page\s*\{[^}]*min-height:\s*100vh/)
  })

  it('ErrorView route loader points at the view', async () => {
    const loaded = (await VIEW_LOADERS.ErrorView()) as { default?: unknown }

    expect(loaded.default).toBe(ErrorView)
  })
})
