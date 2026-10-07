import { DOMWrapper, flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { adminHttp } from '@/api/http'
import type { JsonSchema, Setting } from '@/api/settings'
import { createApiMock, mountWithApp } from '@/test/helpers'
import SettingCard from './SettingCard.vue'
import settingCardSource from './SettingCard.vue?raw'

// Pydantic 風格的 schema fixture（BACKEND-106 registry）
const TIME = '^([01]\\d|2[0-3]):[0-5]\\d$'

const PICKUP_WINDOW: JsonSchema = {
  title: '接送時段',
  type: 'object',
  additionalProperties: false,
  required: ['request_start', 'request_end', 'latest_expected_arrival', 'auto_expire_minutes'],
  properties: {
    request_start: { type: 'string', pattern: TIME, title: '可發起接送開始時間' },
    request_end: { type: 'string', pattern: TIME, title: '可發起接送結束時間' },
    latest_expected_arrival: { type: 'string', pattern: TIME, title: '最晚預計抵達時間' },
    auto_expire_minutes: { type: 'integer', minimum: 10, maximum: 600, title: '接送請求自動過期分鐘數' },
  },
}

const LINE_MESSAGING: JsonSchema = {
  title: 'LINE 訊息推播',
  type: 'object',
  additionalProperties: false,
  properties: {
    channel_access_token: { anyOf: [{ type: 'string' }, { type: 'null' }], title: 'Channel access token' },
    channel_secret: { anyOf: [{ type: 'string' }, { type: 'null' }], title: 'Channel secret' },
  },
}

const LINE_LIFF: JsonSchema = {
  title: 'LINE 登入（LIFF）',
  type: 'object',
  additionalProperties: false,
  required: ['liff_id'],
  properties: {
    liff_id: { type: 'string', pattern: '^$|^\\d+-[A-Za-z0-9]+$', title: 'LIFF 應用程式 ID' },
  },
}

const PICKUP_SETTING: Setting = {
  key: 'pickup.window',
  group: 'pickup',
  label: '接送時段',
  is_secret: false,
  value: { request_start: '12:00', request_end: '19:00', latest_expected_arrival: '19:00', auto_expire_minutes: 120 },
  json_schema: PICKUP_WINDOW,
  updated_at: '2026-10-02T08:05:00Z',
  updated_by_name: '張主任',
}

const MESSAGING_SETTING: Setting = {
  key: 'line.messaging',
  group: 'line',
  label: 'LINE 訊息推播',
  is_secret: true,
  value: { channel_access_token: '****abcd', channel_secret: null },
  json_schema: LINE_MESSAGING,
  updated_at: null,
  updated_by_name: null,
}

const LIFF_SETTING: Setting = {
  key: 'line.liff',
  group: 'line',
  label: 'LINE 登入（LIFF）',
  is_secret: false,
  value: { liff_id: '1234567890-AbCdEfGh' },
  json_schema: LINE_LIFF,
  updated_at: null,
  updated_by_name: null,
}

const WRITER = ['settings:read', 'settings:write']
const READER = ['settings:read']

let mock: MockAdapter

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
}

/** el-form-item 的錯誤訊息有 100ms debounce，斷言錯誤文字前等過 */
async function settleErrors(): Promise<void> {
  await settle()
  await new Promise((resolve) => setTimeout(resolve, 150))
  await settle()
}

async function mountCard(setting: Setting, permissions: string[] = WRITER): Promise<VueWrapper> {
  const { wrapper } = await mountWithApp(SettingCard, {
    props: { setting },
    piniaInitialState: {
      auth: {
        status: 'authenticated',
        user: {
          id: 'u1',
          username: 'director01',
          display_name: '張主任',
          role: { id: 'r-director', code: 'director', name: '主任' },
          permissions,
          must_change_password: false,
        },
      },
    },
  })
  await settle()
  return wrapper as VueWrapper
}

function field(wrapper: VueWrapper, path: string): DOMWrapper<Element> {
  const item = wrapper.find(`[data-test="field-${path}"]`)
  if (!item.exists()) throw new Error(`找不到欄位 ${path}`)
  return item
}

function button(wrapper: VueWrapper, text: string): DOMWrapper<Element> | undefined {
  return wrapper.findAll('button').find((b) => b.text() === text)
}

async function click(wrapper: VueWrapper | DOMWrapper<Element>, text: string): Promise<void> {
  const btn = wrapper.findAll('button').find((b) => b.text() === text)
  if (!btn) throw new Error(`找不到按鈕「${text}」`)
  await btn.trigger('click')
  await settle()
}

async function setExpireMinutes(wrapper: VueWrapper, value: string): Promise<void> {
  const input = field(wrapper, 'auto_expire_minutes').find('input')
  await input.setValue(value)
  await input.trigger('change')
  await settle()
}

async function confirmMessageBox(text: string): Promise<void> {
  const box = Array.from(document.body.querySelectorAll<HTMLElement>('.el-message-box')).at(-1)
  const btn = Array.from(box?.querySelectorAll<HTMLButtonElement>('button') ?? []).find(
    (b) => b.textContent?.trim() === text,
  )
  if (!btn) throw new Error(`message box 找不到「${text}」`)
  btn.click()
  await settle()
}

function putBody(index = 0): { value: Record<string, unknown> } {
  const call = mock.history.put[index]
  if (!call) throw new Error(`沒有第 ${index + 1} 個 PUT`)
  return JSON.parse(call.data as string) as { value: Record<string, unknown> }
}

function invalid(details: { loc: (string | number)[]; msg: string }[]) {
  return { error: { code: 'invalid_setting_value', message: '設定值不符合格式', details } }
}

describe('SettingCard', () => {
  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    document.body.innerHTML = ''
  })

  it('SettingCard saves changed value', async () => {
    mock.onPut('/admin/settings/pickup.window').reply((config) => {
      const body = JSON.parse(config.data as string) as { value: Record<string, unknown> }
      return [200, { ...PICKUP_SETTING, value: body.value, updated_at: '2026-10-07T08:30:00Z', updated_by_name: '林主任' }]
    })
    const wrapper = await mountCard(PICKUP_SETTING)

    await setExpireMinutes(wrapper, '90')
    await click(wrapper, '儲存')

    expect(mock.history.put.map((c) => c.url)).toEqual(['/admin/settings/pickup.window'])
    expect(putBody().value.auto_expire_minutes).toBe(90)
    expect(putBody().value.request_start).toBe('12:00')
    expect(document.body.textContent).toContain('已儲存「接送時段」')
    const saved = wrapper.emitted('saved')?.[0]?.[0] as Setting
    expect(saved.key).toBe('pickup.window')
    expect(wrapper.find('.setting-card__meta').text()).toBe('最後更新：2026/10/07 16:30・林主任')
    expect(button(wrapper, '儲存')?.attributes('disabled')).toBeDefined()
  })

  it('SettingCard disables save until dirty and restores', async () => {
    const wrapper = await mountCard(PICKUP_SETTING)
    expect(button(wrapper, '儲存')?.attributes('disabled')).toBeDefined()
    expect(button(wrapper, '還原')?.attributes('disabled')).toBeDefined()

    await setExpireMinutes(wrapper, '90')
    expect(button(wrapper, '儲存')?.attributes('disabled')).toBeUndefined()
    expect(button(wrapper, '還原')?.attributes('disabled')).toBeUndefined()

    await click(wrapper, '還原')
    expect(field(wrapper, 'auto_expire_minutes').find<HTMLInputElement>('input').element.value).toBe('120')
    expect(button(wrapper, '儲存')?.attributes('disabled')).toBeDefined()
    expect(mock.history.put).toHaveLength(0)
  })

  it('SettingCard shows unsaved tag and last updater', async () => {
    const wrapper = await mountCard(PICKUP_SETTING)
    expect(wrapper.find('.setting-card__title').text()).toBe('接送時段')
    expect(wrapper.find('.setting-card__meta').text()).toBe('最後更新：2026/10/02 16:05・張主任')

    await setExpireMinutes(wrapper, '90')
    const tag = wrapper.find('.setting-card__title .el-tag')
    expect(tag.text()).toBe('未儲存')
    expect(tag.classes()).toContain('el-tag--warning')
    document.body.innerHTML = ''

    const fresh = await mountCard(MESSAGING_SETTING)
    expect(fresh.find('.setting-card__meta').text()).toBe('使用預設值')
  })

  it('SettingCard maps 422 details to field errors', async () => {
    mock
      .onPut('/admin/settings/pickup.window')
      .reply(422, invalid([{ loc: ['request_start'], msg: '開始時間需早於結束時間' }]))
    const wrapper = await mountCard(PICKUP_SETTING)

    await setExpireMinutes(wrapper, '90')
    await click(wrapper, '儲存')
    await settleErrors()

    expect(field(wrapper, 'request_start').find('.el-form-item__error').text()).toBe('開始時間需早於結束時間')
    const alert = wrapper.find('.setting-card__alert')
    expect(alert.text()).toContain('部分欄位不正確')
    expect(alert.text()).toContain('請修正標示紅字的欄位後再儲存')
    expect(wrapper.emitted('saved')).toBeUndefined()
  })

  it('SettingCard shows cross-field 422 in alert', async () => {
    mock
      .onPut('/admin/settings/pickup.window')
      .reply(422, invalid([{ loc: [], msg: 'Value error, 可發起接送開始時間必須早於結束時間' }]))
    const wrapper = await mountCard(PICKUP_SETTING)

    await setExpireMinutes(wrapper, '90')
    await click(wrapper, '儲存')

    const alert = wrapper.find('.setting-card__alert')
    expect(alert.text()).toContain('部分欄位不正確')
    expect(alert.findAll('li').map((li) => li.text())).toEqual(['可發起接送開始時間必須早於結束時間'])
    expect(wrapper.text()).not.toContain('Value error')
    expect(wrapper.text()).not.toContain('請修正標示紅字的欄位後再儲存')
    // alert 在表單上方
    const form = wrapper.find('.el-form').element
    expect(alert.element.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
  })

  it('SettingCard shows other errors as message', async () => {
    mock
      .onPut('/admin/settings/pickup.window')
      .reply(500, { error: { code: 'internal_error', message: '伺服器暫時沒有回應，請稍後再試', details: null } })
    const wrapper = await mountCard(PICKUP_SETTING)

    await setExpireMinutes(wrapper, '90')
    await click(wrapper, '儲存')

    expect(document.body.textContent).toContain('伺服器暫時沒有回應，請稍後再試')
    expect(wrapper.find('.setting-card__alert').exists()).toBe(false)
    expect(button(wrapper, '儲存')?.attributes('disabled')).toBeUndefined()
  })

  it('SettingCard is readonly without settings:write', async () => {
    const wrapper = await mountCard(PICKUP_SETTING, READER)

    expect(button(wrapper, '儲存')).toBeUndefined()
    expect(button(wrapper, '還原')).toBeUndefined()
    expect(wrapper.find('.setting-card__readonly').text()).toBe('你只有檢視權限')
    const inputs = wrapper.findAll<HTMLInputElement>('input')
    expect(inputs.length).toBeGreaterThan(0)
    expect(inputs.every((i) => i.element.disabled)).toBe(true)
  })

  it('SettingCard is readonly while saving', async () => {
    let release: () => void = () => undefined
    mock.onPut('/admin/settings/pickup.window').reply(
      () =>
        new Promise((resolve) => {
          release = () => resolve([200, { ...PICKUP_SETTING, value: { ...PICKUP_SETTING.value, auto_expire_minutes: 90 } }])
        }),
    )
    const wrapper = await mountCard(PICKUP_SETTING)
    await setExpireMinutes(wrapper, '90')
    await click(wrapper, '儲存')

    expect(button(wrapper, '還原')?.attributes('disabled')).toBeDefined()
    expect(button(wrapper, '儲存')?.classes()).toContain('is-loading')
    expect(wrapper.findAll<HTMLInputElement>('input').every((i) => i.element.disabled)).toBe(true)

    release()
    await settle()
    expect(field(wrapper, 'auto_expire_minutes').find<HTMLInputElement>('input').element.disabled).toBe(false)
  })

  it('SettingCard passes secret fields for secret settings', async () => {
    const wrapper = await mountCard(MESSAGING_SETTING)

    expect(field(wrapper, 'channel_access_token').text()).toContain('已設定（****abcd）')
    expect(field(wrapper, 'channel_secret').text()).toContain('未設定')
    expect(wrapper.findAll('.secret-input')).toHaveLength(2)
    expect(wrapper.findAll<HTMLInputElement>('input').some((i) => i.element.value === '****abcd')).toBe(false)
  })

  it('SettingCard rebuilds form after clearing a secret', async () => {
    mock.onPut('/admin/settings/line.messaging').reply(200, {
      ...MESSAGING_SETTING,
      value: { channel_access_token: null, channel_secret: null },
      updated_at: '2026-10-07T08:30:00Z',
      updated_by_name: '張主任',
    })
    const wrapper = await mountCard(MESSAGING_SETTING)

    await click(field(wrapper, 'channel_access_token'), '清除')
    await confirmMessageBox('確定')
    expect(field(wrapper, 'channel_access_token').text()).toContain('將於儲存後清除')
    await click(wrapper, '儲存')

    expect(putBody().value).toEqual({ channel_access_token: null, channel_secret: null })
    expect(field(wrapper, 'channel_access_token').text()).toContain('未設定')
    expect(field(wrapper, 'channel_access_token').text()).not.toContain('將於儲存後清除')
  })

  it('SettingCard blocks save on client validation errors', async () => {
    const wrapper = await mountCard(LIFF_SETTING)

    const input = field(wrapper, 'liff_id').find('input')
    await input.setValue('abc')
    await settle()
    await click(wrapper, '儲存')
    await settleErrors()

    expect(mock.history.put).toHaveLength(0)
    expect(field(wrapper, 'liff_id').find('.el-form-item__error').text()).toBe('格式不正確')
    expect(wrapper.find('.setting-card__alert').exists()).toBe(false)
  })

  it('SettingCard exposes isDirty and reset', async () => {
    mock
      .onPut('/admin/settings/pickup.window')
      .reply(422, invalid([{ loc: [], msg: 'Value error, 可發起接送開始時間必須早於結束時間' }]))
    const wrapper = await mountCard(PICKUP_SETTING)
    const exposed = wrapper.vm as unknown as { isDirty: boolean; reset: () => void }
    expect(exposed.isDirty).toBe(false)

    await setExpireMinutes(wrapper, '90')
    await click(wrapper, '儲存')
    expect(exposed.isDirty).toBe(true)
    expect(wrapper.find('.setting-card__alert').exists()).toBe(true)

    exposed.reset()
    await settle()
    expect(exposed.isDirty).toBe(false)
    expect(wrapper.find('.setting-card__alert').exists()).toBe(false)
    expect(field(wrapper, 'auto_expire_minutes').find<HTMLInputElement>('input').element.value).toBe('120')
  })

  it('SettingCard lays out header, body and footer', async () => {
    const wrapper = await mountCard(PICKUP_SETTING)

    expect(wrapper.find('section.setting-card > header.setting-card__head').exists()).toBe(true)
    expect(wrapper.find('section.setting-card > footer.setting-card__foot').exists()).toBe(true)
    expect(wrapper.findAll('.setting-card__foot button').map((b) => b.text())).toEqual(['還原', '儲存'])
    // happy-dom 不算版面：鎖住稿的標題字級與 footer 靠右
    const style = (settingCardSource.match(/<style scoped>([\s\S]*?)<\/style>/)?.[1] ?? '').replace(/\s+/g, ' ')
    expect(style).toMatch(/\.setting-card__title \{[^}]*font-size: 16px;[^}]*font-weight: 600;/)
    expect(style).toMatch(/\.setting-card__foot \{[^}]*justify-content: flex-end;/)
  })
})
