import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createTestingPinia } from '@pinia/testing'
import type MockAdapter from 'axios-mock-adapter'
import { ElMessage } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import type { StaffMe } from '@/api/auth'
import { adminHttp } from '@/api/http'
import { VIEW_LOADERS } from '@/router/viewLoaders'
import { useAuthStore } from '@/stores/auth'
import { createApiMock, mountWithApp } from '@/test/helpers'
import ChangePasswordView from './ChangePasswordView.vue'
import changePasswordViewSource from './ChangePasswordView.vue?raw'

const CHANGE_URL = '/admin/auth/change-password'

function makeUser(overrides: Partial<StaffMe> = {}): StaffMe {
  return {
    id: 'u1',
    username: 'clerk01',
    display_name: '林行政',
    role: { id: 'r1', code: 'clerk', name: '行政' },
    permissions: ['attendance:read'],
    must_change_password: false,
    ...overrides,
  }
}

let mock: MockAdapter

/** el-form-item 錯誤訊息有 100ms debounce（setTimeout），以 fake timers 推進 */
async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
  await vi.advanceTimersByTimeAsync(150)
  await flushPromises()
}

async function mountView(user: Partial<StaffMe> = {}, initialRoute = '/change-password') {
  const mounted = await mountWithApp(ChangePasswordView, {
    initialRoute,
    piniaInitialState: { auth: { user: makeUser(user), status: 'authenticated' } },
  })
  await settle()
  return mounted
}

function input(wrapper: VueWrapper, name: 'current' | 'next' | 'confirm') {
  const el = wrapper.find(`input[data-test=cp-${name}]`)
  if (!el.exists()) throw new Error(`找不到欄位 ${name}`)
  return el
}

function formItem(wrapper: VueWrapper, label: string) {
  const item = wrapper.findAll('.el-form-item').find((i) => i.find('.el-form-item__label').text() === label)
  if (!item) throw new Error(`找不到欄位「${label}」`)
  return item
}

function fieldError(wrapper: VueWrapper, label: string): string {
  const error = formItem(wrapper, label).find('.el-form-item__error')
  return error.exists() ? error.text() : ''
}

function ruleState(wrapper: VueWrapper, label: string): string | undefined {
  const rule = wrapper.findAll('.pw-rule').find((r) => r.text().startsWith(label))
  if (!rule) throw new Error(`找不到規則「${label}」`)
  return rule.attributes('data-state')
}

function buttonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('button').find((b) => b.text() === text)
}

async function submit(wrapper: VueWrapper): Promise<void> {
  ;(wrapper.find('button[type=submit]').element as HTMLButtonElement).click()
  await settle()
}

async function fill(wrapper: VueWrapper, current: string, next: string, confirm: string): Promise<void> {
  await input(wrapper, 'current').setValue(current)
  await input(wrapper, 'next').setValue(next)
  await input(wrapper, 'confirm').setValue(confirm)
}

describe('ChangePasswordView', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('ChangePasswordView validates rules client side', async () => {
    const { wrapper } = await mountView()

    await input(wrapper, 'current').setValue('Tmp8f3K2q9x')
    await input(wrapper, 'next').setValue('abc123')
    expect(ruleState(wrapper, '至少 10 碼')).toBe('fail')
    expect(ruleState(wrapper, '含英文字母')).toBe('ok')
    expect(ruleState(wrapper, '含數字')).toBe('ok')

    await input(wrapper, 'confirm').setValue('abc124')
    await input(wrapper, 'confirm').trigger('blur')
    await settle()
    expect(wrapper.text()).toContain('兩次輸入的新密碼不一致')

    await submit(wrapper)
    expect(mock.history.post.length).toBe(0)
    expect(fieldError(wrapper, '新密碼')).toBe('新密碼不符合規則')
  })

  it('ChangePasswordView rule list starts pending and reports each rule', async () => {
    const { wrapper } = await mountView()
    for (const label of ['至少 10 碼', '含英文字母', '含數字']) {
      expect(ruleState(wrapper, label)).toBe('pending')
    }

    await input(wrapper, 'next').setValue('1234567890')
    expect(ruleState(wrapper, '至少 10 碼')).toBe('ok')
    expect(ruleState(wrapper, '含英文字母')).toBe('fail')
    expect(ruleState(wrapper, '含數字')).toBe('ok')
    // 不只靠顏色：螢幕報讀文字
    const letterRule = wrapper.findAll('.pw-rule').find((r) => r.text().startsWith('含英文字母'))
    expect(letterRule?.text()).toContain('（未符合）')
    const lengthRule = wrapper.findAll('.pw-rule').find((r) => r.text().startsWith('至少 10 碼'))
    expect(lengthRule?.text()).toContain('（已符合）')
  })

  it('ChangePasswordView compares the confirmation only after blur', async () => {
    const { wrapper } = await mountView()
    await input(wrapper, 'next').setValue('New0000000a')
    await input(wrapper, 'confirm').setValue('New0000000b')
    await settle()
    expect(wrapper.text()).not.toContain('兩次輸入的新密碼不一致')

    await input(wrapper, 'confirm').trigger('blur')
    await settle()
    expect(fieldError(wrapper, '確認新密碼')).toBe('兩次輸入的新密碼不一致')

    // 之後改任一欄即時更新
    await input(wrapper, 'next').setValue('New0000000b')
    await settle()
    expect(fieldError(wrapper, '確認新密碼')).toBe('')
  })

  it('ChangePasswordView requires every field on submit', async () => {
    const { wrapper } = await mountView()

    await submit(wrapper)

    expect(fieldError(wrapper, '目前密碼')).toBe('請輸入目前密碼')
    expect(fieldError(wrapper, '新密碼')).toBe('請輸入新密碼')
    expect(fieldError(wrapper, '確認新密碼')).toBe('請再次輸入新密碼')
    expect(mock.history.post.length).toBe(0)
  })

  it('ChangePasswordView rejects a new password equal to the current one', async () => {
    const { wrapper } = await mountView()
    await fill(wrapper, 'Same000000a', 'Same000000a', 'Same000000a')

    await submit(wrapper)

    expect(fieldError(wrapper, '新密碼')).toBe('新密碼不可與目前密碼相同')
    expect(mock.history.post.length).toBe(0)
  })

  it('ChangePasswordView submits and redirects', async () => {
    mock.onPost(CHANGE_URL).reply(200, { user: makeUser({ permissions: ['attendance:read'] }) })
    const { wrapper, router } = await mountView({ must_change_password: true })
    await fill(wrapper, 'Old0000000a', 'New0000000a', 'New0000000a')

    await submit(wrapper)

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({
      current_password: 'Old0000000a',
      new_password: 'New0000000a',
    })
    expect(router.currentRoute.value.path).toBe('/attendance')
    expect(document.body.textContent).toContain('密碼已更新')
    expect(useAuthStore().mustChangePassword).toBe(false)
  })

  it('ChangePasswordView without any usable page goes to forbidden after success', async () => {
    mock.onPost(CHANGE_URL).reply(200, { user: makeUser({ permissions: [] }) })
    const { wrapper, router } = await mountView()
    await fill(wrapper, 'Old0000000a', 'New0000000a', 'New0000000a')

    await submit(wrapper)

    expect(router.currentRoute.value.path).toBe('/forbidden')
  })

  it('ChangePasswordView maps backend errors to fields', async () => {
    mock.onPost(CHANGE_URL).replyOnce(400, {
      error: { code: 'current_password_incorrect', message: '目前密碼不正確', details: null },
    })
    const { wrapper, router } = await mountView()
    await fill(wrapper, 'Old0000000a', 'New0000000a', 'New0000000a')

    await submit(wrapper)
    expect(fieldError(wrapper, '目前密碼')).toBe('目前密碼不正確')

    mock.onPost(CHANGE_URL).replyOnce(422, {
      error: { code: 'weak_password', message: '密碼強度不足', details: { reasons: ['至少一個數字', '不可與帳號相同'] } },
    })
    await input(wrapper, 'current').setValue('Old0000000b')
    await submit(wrapper)
    expect(fieldError(wrapper, '目前密碼')).toBe('')
    expect(fieldError(wrapper, '新密碼')).toBe('至少一個數字、不可與帳號相同')
    expect(router.currentRoute.value.path).toBe('/change-password')
  })

  it('ChangePasswordView maps unchanged and reasonless weak password errors', async () => {
    mock.onPost(CHANGE_URL).replyOnce(422, {
      error: { code: 'password_unchanged', message: '新密碼不可與目前密碼相同', details: null },
    })
    const { wrapper } = await mountView()
    await fill(wrapper, 'Old0000000a', 'New0000000a', 'New0000000a')
    await submit(wrapper)
    expect(fieldError(wrapper, '新密碼')).toBe('新密碼不可與目前密碼相同')

    mock.onPost(CHANGE_URL).replyOnce(422, {
      error: { code: 'weak_password', message: '密碼強度不足：過於常見', details: null },
    })
    await submit(wrapper)
    expect(fieldError(wrapper, '新密碼')).toBe('密碼強度不足：過於常見')
  })

  it('ChangePasswordView clears a field error when the field changes', async () => {
    mock.onPost(CHANGE_URL).replyOnce(400, {
      error: { code: 'current_password_incorrect', message: '目前密碼不正確', details: null },
    })
    const { wrapper } = await mountView()
    await fill(wrapper, 'Old0000000a', 'New0000000a', 'New0000000a')
    await submit(wrapper)
    expect(fieldError(wrapper, '目前密碼')).toBe('目前密碼不正確')

    await input(wrapper, 'current').setValue('Old0000000c')
    await settle()

    expect(fieldError(wrapper, '目前密碼')).toBe('')
  })

  it('ChangePasswordView shows throttle and network errors as messages', async () => {
    const error = vi.spyOn(ElMessage, 'error').mockReturnValue({ close: vi.fn() })
    mock.onPost(CHANGE_URL).replyOnce(429, {
      error: { code: 'too_many_attempts', message: '嘗試次數過多，請 15 分鐘後再試', details: null },
    })
    const { wrapper } = await mountView()
    await fill(wrapper, 'Old0000000a', 'New0000000a', 'New0000000a')

    await submit(wrapper)
    expect(error).toHaveBeenLastCalledWith('嘗試次數過多，請 15 分鐘後再試')

    mock.onPost(CHANGE_URL).networkError()
    await submit(wrapper)
    expect(error).toHaveBeenLastCalledWith('無法連線伺服器，請稍後再試')
    expect(fieldError(wrapper, '目前密碼')).toBe('')
    expect(fieldError(wrapper, '新密碼')).toBe('')
  })

  it('ChangePasswordView disables the form while submitting', async () => {
    mock.onPost(CHANGE_URL).reply(() => new Promise(() => {}))
    const { wrapper } = await mountView()
    await fill(wrapper, 'Old0000000a', 'New0000000a', 'New0000000a')

    await submit(wrapper)

    expect(wrapper.find('button[type=submit]').classes()).toContain('is-loading')
    for (const name of ['current', 'next', 'confirm'] as const) {
      expect(input(wrapper, name).attributes('disabled'), name).toBeDefined()
    }
    expect(buttonByText(wrapper, '返回')?.attributes('disabled')).toBeDefined()
    await submit(wrapper)
    expect(mock.history.post.length).toBe(1)
  })

  it('ChangePasswordView forced mode hides back button', async () => {
    const forced = await mountView({ must_change_password: true })
    expect(forced.wrapper.text()).toContain('首次登入或密碼已被重設，請先修改密碼')
    expect(forced.wrapper.find('.el-alert').classes()).toContain('el-alert--warning')
    expect(buttonByText(forced.wrapper, '返回')).toBeUndefined()
    expect(forced.wrapper.text()).toContain('請輸入管理員提供的臨時密碼')

    const normal = await mountView({ must_change_password: false })
    expect(normal.wrapper.text()).not.toContain('首次登入或密碼已被重設，請先修改密碼')
    expect(normal.wrapper.text()).not.toContain('請輸入管理員提供的臨時密碼')
    expect(buttonByText(normal.wrapper, '返回')).toBeDefined()
  })

  it('ChangePasswordView forced mode offers logout', async () => {
    mock.onPost('/admin/auth/logout').reply(200, { message: '已登出' })
    const forced = await mountView({ must_change_password: true })
    const logout = buttonByText(forced.wrapper, '登出')
    expect(logout).toBeDefined()

    await logout?.trigger('click')
    await settle()

    expect(mock.history.post.map((c) => c.url)).toContain('/admin/auth/logout')
    expect(forced.router.currentRoute.value.path).toBe('/login')
    expect(useAuthStore().status).toBe('anonymous')

    const normal = await mountView({ must_change_password: false })
    expect(buttonByText(normal.wrapper, '登出')).toBeUndefined()
  })

  it('ChangePasswordView hides the temporary password hint when the field has an error', async () => {
    const { wrapper } = await mountView({ must_change_password: true })

    await submit(wrapper)

    expect(fieldError(wrapper, '目前密碼')).toBe('請輸入目前密碼')
    expect(wrapper.text()).not.toContain('請輸入管理員提供的臨時密碼')
  })

  it('ChangePasswordView shows which account is being changed', async () => {
    const { wrapper } = await mountView()

    expect(wrapper.find('h1').text()).toBe('修改密碼')
    expect(wrapper.text()).toContain('林行政（帳號 clerk01）')
  })

  it('ChangePasswordView back without history goes to the first allowed page', async () => {
    const { wrapper, router } = await mountView({ permissions: ['homework:read'] })

    await buttonByText(wrapper, '返回')?.trigger('click')
    await settle()

    expect(router.currentRoute.value.path).toBe('/homework')
  })

  it('ChangePasswordView back returns to the previous page', async () => {
    // web history 才有 history.state.back（memory history 沒有）
    const router = createRouter({
      history: createWebHashHistory(),
      routes: [{ path: '/:pathMatch(.*)*', component: { render: () => null } }],
    })
    await router.push('/students')
    await router.push('/change-password')
    const pinia = createTestingPinia({
      initialState: { auth: { user: makeUser({ permissions: ['homework:read'] }), status: 'authenticated' } },
      stubActions: false,
      createSpy: vi.fn,
    })
    const wrapper = mount(ChangePasswordView, { global: { plugins: [router, pinia] } })
    await settle()

    await buttonByText(wrapper, '返回')?.trigger('click')
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/students'))
    history.replaceState(null, '', '/')
  })

  it('ChangePasswordView layout rules follow the approved mockup', () => {
    // happy-dom 不計算版面：卡片最寬 440、手機內距與送出鈕撐滿以原始碼斷言
    const style = changePasswordViewSource.slice(changePasswordViewSource.indexOf('<style'))
    expect(style).toMatch(/\.cp\s*\{[^}]*max-width:\s*440px/)
    expect(style).toMatch(/\.cp-page\s*\{[^}]*min-height:\s*100vh/)
    expect(style).toMatch(/@media\s*\(max-width:\s*767px\)\s*\{[^@]*\.cp\s*\{[^}]*padding:\s*24px 20px 20px/)
    expect(style).toMatch(/@media\s*\(max-width:\s*767px\)\s*\{[^@]*\.cp__submit\s*\{[^}]*flex:\s*1/)
    expect(changePasswordViewSource).toMatch(/autocomplete="current-password"/)
    expect(changePasswordViewSource.match(/autocomplete="new-password"/g)).toHaveLength(2)
  })

  it('ChangePasswordView route loader points at the view', async () => {
    const loaded = (await VIEW_LOADERS.ChangePasswordView()) as { default?: unknown }

    expect(loaded.default).toBe(ChangePasswordView)
  })
})
