import { flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import type { StaffMe } from '@/api/auth'
import { adminHttp } from '@/api/http'
import { VIEW_LOADERS } from '@/router/viewLoaders'
import { useAuthStore } from '@/stores/auth'
import { createApiMock, mountWithApp } from '@/test/helpers'
import LoginView from './LoginView.vue'
import loginViewSource from './LoginView.vue?raw'

const CONFIG_URL = '/parent/config'
const LOGIN_URL = '/admin/auth/login'

function makeUser(overrides: Partial<StaffMe> = {}): StaffMe {
  return {
    id: 'u1',
    username: 'clerk01',
    display_name: '林行政',
    role: { id: 'r1', code: 'clerk', name: '行政' },
    permissions: ['attendance:read', 'leaves:read'],
    must_change_password: false,
    ...overrides,
  }
}

let mock: MockAdapter

/** el-form-item 的錯誤訊息有 100ms debounce（以 setTimeout 排程），以 fake timers 推進 */
async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
  await vi.advanceTimersByTimeAsync(150)
  await flushPromises()
}

async function mountLogin(initialRoute = '/login', auth: Record<string, unknown> = {}) {
  const mounted = await mountWithApp(LoginView, {
    initialRoute,
    piniaInitialState: { auth: { user: null, status: 'anonymous', restoreError: null, ...auth } },
  })
  // 讓 focus() 生效以斷言 document.activeElement（元件根元素移進 document 不影響 Vue）
  document.body.appendChild(mounted.wrapper.element)
  await settle()
  return mounted
}

function usernameInput(wrapper: VueWrapper) {
  return wrapper.find('input[autocomplete=username]')
}

function passwordInput(wrapper: VueWrapper) {
  return wrapper.find('input[autocomplete=current-password]')
}

function submitButton(wrapper: VueWrapper) {
  return wrapper.find('button[type=submit]')
}

/** 等同在欄位按 Enter：瀏覽器的隱式送出是對預設送出鈕派發 click（happy-dom 不實作隱式送出） */
async function submit(wrapper: VueWrapper): Promise<void> {
  ;(submitButton(wrapper).element as HTMLButtonElement).click()
  await settle()
}

async function fillAndSubmit(wrapper: VueWrapper, username: string, password: string): Promise<void> {
  await usernameInput(wrapper).setValue(username)
  await passwordInput(wrapper).setValue(password)
  await submit(wrapper)
}

function alertText(wrapper: VueWrapper): string {
  return wrapper.find('.el-alert').exists() ? wrapper.find('.el-alert').text() : ''
}

describe('LoginView', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    mock = createApiMock(adminHttp)
    mock.onGet(CONFIG_URL).reply(200, { liff_id: 'liff-x', org_name: '快樂安親班', logo_url: null })
  })

  afterEach(() => {
    mock.restore()
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('LoginView validates required fields', async () => {
    const { wrapper } = await mountLogin()

    await submit(wrapper)

    expect(wrapper.text()).toContain('請輸入帳號')
    expect(wrapper.text()).toContain('請輸入密碼')
    expect(mock.history.post.length).toBe(0)
    // 游標移到第一個錯誤欄位
    expect(document.activeElement).toBe(usernameInput(wrapper).element)
  })

  it('LoginView treats a blank username as missing and focuses the first invalid field', async () => {
    const { wrapper } = await mountLogin()

    await usernameInput(wrapper).setValue('   ')
    await submit(wrapper)
    expect(wrapper.text()).toContain('請輸入帳號')
    expect(document.activeElement).toBe(usernameInput(wrapper).element)

    await usernameInput(wrapper).setValue('clerk01')
    await submit(wrapper)
    expect(wrapper.text()).not.toContain('請輸入帳號')
    expect(wrapper.text()).toContain('請輸入密碼')
    expect(document.activeElement).toBe(passwordInput(wrapper).element)
    expect(mock.history.post.length).toBe(0)
  })

  it('LoginView clears a field error when the user types', async () => {
    const { wrapper } = await mountLogin()
    await submit(wrapper)
    expect(wrapper.text()).toContain('請輸入帳號')

    await usernameInput(wrapper).setValue('c')
    await settle()

    expect(wrapper.text()).not.toContain('請輸入帳號')
    expect(wrapper.text()).toContain('請輸入密碼')
  })

  it('LoginView posts trimmed credentials and redirects', async () => {
    mock.onPost(LOGIN_URL).reply(200, { user: makeUser({ permissions: ['attendance:read', 'leaves:read'] }) })
    const { wrapper, router } = await mountLogin('/login?redirect=/leaves')

    await fillAndSubmit(wrapper, ' clerk01 ', 'Passw0rd123')

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({ username: 'clerk01', password: 'Passw0rd123' })
    expect(router.currentRoute.value.path).toBe('/leaves')
    expect(useAuthStore().user?.username).toBe('clerk01')
  })

  it('LoginView without redirect lands on the first allowed page', async () => {
    mock.onPost(LOGIN_URL).reply(200, { user: makeUser({ permissions: ['homework:read'] }) })
    const { wrapper, router } = await mountLogin()

    await fillAndSubmit(wrapper, 'clerk01', 'Passw0rd123')

    expect(router.currentRoute.value.path).toBe('/homework')
  })

  it('LoginView sends must-change-password users to change password', async () => {
    mock.onPost(LOGIN_URL).reply(200, { user: makeUser({ must_change_password: true }) })
    const { wrapper, router } = await mountLogin('/login?redirect=/leaves')

    await fillAndSubmit(wrapper, 'clerk01', 'Tmp8f3K2q9x')

    expect(router.currentRoute.value.path).toBe('/change-password')
  })

  it('LoginView without any usable page goes to forbidden', async () => {
    mock.onPost(LOGIN_URL).reply(200, { user: makeUser({ permissions: [] }) })
    const { wrapper, router } = await mountLogin()

    await fillAndSubmit(wrapper, 'clerk01', 'Passw0rd123')

    expect(router.currentRoute.value.path).toBe('/forbidden')
  })

  it('LoginView shows invalid credentials error and clears password', async () => {
    mock.onPost(LOGIN_URL).reply(401, {
      error: { code: 'invalid_credentials', message: '帳號或密碼錯誤', details: null },
    })
    const { wrapper, router } = await mountLogin()

    await fillAndSubmit(wrapper, 'clerk01', 'wrong-password')

    expect(wrapper.text()).toContain('帳號或密碼錯誤')
    expect(wrapper.find('.el-alert').classes()).toContain('el-alert--error')
    expect((passwordInput(wrapper).element as HTMLInputElement).value).toBe('')
    expect((usernameInput(wrapper).element as HTMLInputElement).value).toBe('clerk01')
    expect(document.activeElement).toBe(passwordInput(wrapper).element)
    expect(router.currentRoute.value.path).toBe('/login')
  })

  it('LoginView shows throttle and network errors', async () => {
    mock
      .onPost(LOGIN_URL)
      .replyOnce(429, { error: { code: 'too_many_attempts', message: '嘗試次數過多，請 15 分鐘後再試', details: null } })
    const { wrapper } = await mountLogin()

    await fillAndSubmit(wrapper, 'clerk01', 'Passw0rd123')
    expect(alertText(wrapper)).toContain('嘗試次數過多，請 15 分鐘後再試')
    expect(wrapper.find('.el-alert').classes()).toContain('el-alert--error')
    // 不做倒數、按鈕不停用
    expect(submitButton(wrapper).attributes('disabled')).toBeUndefined()

    mock.onPost(LOGIN_URL).networkError()
    await submit(wrapper)
    expect(alertText(wrapper)).toContain('無法連線伺服器，請稍後再試')
    expect(alertText(wrapper)).not.toContain('嘗試次數過多')
    expect(wrapper.find('.el-alert').classes()).toContain('el-alert--warning')
  })

  it('LoginView shows the network message on timeout', async () => {
    mock.onPost(LOGIN_URL).timeout()
    const { wrapper } = await mountLogin()

    await fillAndSubmit(wrapper, 'clerk01', 'Passw0rd123')

    expect(alertText(wrapper)).toContain('無法連線伺服器，請稍後再試')
  })

  it('LoginView shows the network message when restoring the session failed', async () => {
    const { wrapper } = await mountLogin('/login', { restoreError: { code: 'network_error' } })

    expect(alertText(wrapper)).toContain('無法連線伺服器，請稍後再試')
    expect(wrapper.find('.el-alert').classes()).toContain('el-alert--warning')
  })

  it('LoginView disables the form while submitting', async () => {
    mock.onPost(LOGIN_URL).reply(() => new Promise(() => {}))
    const { wrapper } = await mountLogin()

    await fillAndSubmit(wrapper, 'clerk01', 'Passw0rd123')

    expect(submitButton(wrapper).text()).toBe('登入中…')
    expect(submitButton(wrapper).classes()).toContain('is-loading')
    expect(usernameInput(wrapper).attributes('disabled')).toBeDefined()
    expect(passwordInput(wrapper).attributes('disabled')).toBeDefined()

    await submit(wrapper)
    expect(mock.history.post.length).toBe(1)
  })

  it('LoginView shows org name from public config', async () => {
    const { wrapper } = await mountLogin()
    expect(wrapper.find('h1').text()).toBe('快樂安親班')
    expect(wrapper.find('.login__logo.is-fallback').exists()).toBe(true)
    expect(wrapper.text()).toContain('員工登入')

    mock.onGet(CONFIG_URL).reply(500, { error: { code: 'internal_error', message: '伺服器錯誤', details: null } })
    const failed = await mountLogin()
    expect(failed.wrapper.find('h1').text()).toBe('安親班管理系統')
    expect(failed.wrapper.find('.el-alert').exists()).toBe(false)
  })

  it('LoginView falls back to the default name when the config name is blank', async () => {
    mock.onGet(CONFIG_URL).reply(200, { liff_id: 'liff-x', org_name: '', logo_url: null })
    const { wrapper } = await mountLogin()

    expect(wrapper.find('h1').text()).toBe('安親班管理系統')
  })

  it('LoginView shows the logo and falls back when the image fails', async () => {
    mock.onGet(CONFIG_URL).reply(200, {
      liff_id: 'liff-x',
      org_name: '快樂安親班',
      logo_url: 'https://files.example.com/org/logo.png',
    })
    const { wrapper } = await mountLogin()

    const img = wrapper.find('img.login__logo')
    expect(img.attributes('src')).toBe('https://files.example.com/org/logo.png')
    expect(img.attributes('alt')).toBe('快樂安親班 Logo')

    await img.trigger('error')
    expect(wrapper.find('img.login__logo').exists()).toBe(false)
    expect(wrapper.find('.login__logo.is-fallback').exists()).toBe(true)
  })

  it('LoginView shows a skeleton instead of a name while the config loads', async () => {
    mock.onGet(CONFIG_URL).reply(() => new Promise(() => {}))
    const { wrapper } = await mountLogin()

    expect(wrapper.find('.login__org-skeleton').exists()).toBe(true)
    expect(wrapper.find('h1').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('安親班管理系統')
  })

  it('LoginView focuses the username on mount', async () => {
    const focus = vi.spyOn(HTMLElement.prototype, 'focus')
    const { wrapper } = await mountLogin()

    // 掛載當下元件尚未移進 document，以實際收到 focus() 的元素斷言
    expect(focus.mock.contexts).toContain(usernameInput(wrapper).element)
  })

  it('LoginView ignores unsafe or unusable redirect', async () => {
    // 該使用者沒有 leaves:read；firstAllowedPath 依路由表順序為 /attendance
    const permissions = ['attendance:read', 'homework:read']
    for (const redirect of ['//evil.example', '/\\evil.example', '/login', '/leaves']) {
      mock.onPost(LOGIN_URL).reply(200, { user: makeUser({ permissions }) })
      const { wrapper, router } = await mountLogin(`/login?redirect=${encodeURIComponent(redirect)}`)
      expect(router.currentRoute.value.query.redirect).toBe(redirect)

      await fillAndSubmit(wrapper, 'clerk01', 'Passw0rd123')

      expect(router.currentRoute.value.path, `redirect=${redirect}`).toBe('/attendance')
      wrapper.unmount()
      document.body.innerHTML = ''
    }
  })

  it('LoginView layout rules follow the approved mockup', () => {
    // happy-dom 不計算版面：卡片最寬 400、Logo 64、送出鈕滿寬高 44、手機縮內距以原始碼斷言
    const style = loginViewSource.slice(loginViewSource.indexOf('<style'))
    expect(style).toMatch(/\.login\s*\{[^}]*max-width:\s*400px/)
    expect(style).toMatch(/\.login__logo\s*\{[^}]*width:\s*64px;[^}]*height:\s*64px/)
    expect(style).toMatch(/\.login__submit\s*\{[^}]*width:\s*100%;[^}]*height:\s*44px/)
    expect(style).toMatch(/@media\s*\(max-width:\s*767px\)\s*\{[^@]*\.login__card\s*\{[^}]*padding:\s*24px 20px 20px/)
    expect(style).toMatch(/\.login-page\s*\{[^}]*min-height:\s*100vh/)
    expect(loginViewSource).toContain('忘記密碼時，請聯絡主任或系統管理員重設')
  })

  it('LoginView route loader points at the view', async () => {
    const loaded = (await VIEW_LOADERS.LoginView()) as { default?: unknown }

    expect(loaded.default).toBe(LoginView)
  })
})
