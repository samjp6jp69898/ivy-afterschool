import type MockAdapter from 'axios-mock-adapter'
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createApiMock, mountWithApp } from '@/test/helpers'
import { parentHttp } from '../api/http'
import { useSnackbarStore } from '../stores/snackbar'
import NotificationPrefsView from './NotificationPrefsView.vue'

const URL = '/parent/notification-preferences'

const PREFS = [
  { event: 'attendance.checked_in', label: '到班通知', line_enabled: true },
  { event: 'attendance.checked_out', label: '離班通知', line_enabled: true },
  { event: 'homework.eta_updated', label: '預計可接送時間', line_enabled: true },
  { event: 'homework.done', label: '作業完成', line_enabled: true },
  { event: 'pickup.replied', label: '老師回覆接送', line_enabled: true },
  { event: 'pickup.completed', label: '接送完成', line_enabled: true },
  { event: 'exam.published', label: '成績公布', line_enabled: false },
]

const SERVER_ERROR = { error: { code: 'internal_error', message: '伺服器錯誤', details: null } }

type Wrapper = Awaited<ReturnType<typeof mountWithApp>>['wrapper']

function switchOf(wrapper: Wrapper, label: string) {
  return wrapper.get(`[role="switch"][aria-label="${label}"]`)
}

function rowOf(wrapper: Wrapper, label: string) {
  const row = wrapper.findAll('.pref-row').find((r) => r.get('.pref-row__text').text() === label)
  if (!row) throw new Error(`找不到 ${label} 那列`)
  return row
}

/** 可手動決定何時回應的 reply */
function deferredReply() {
  let resolve!: (value: [number, unknown]) => void
  const promise = new Promise<[number, unknown]>((r) => {
    resolve = r
  })
  return { reply: () => promise, resolve }
}

describe('NotificationPrefsView', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(parentHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  it('NotificationPrefsView renders seven switches', async () => {
    mock.onGet(URL).reply(200, { items: PREFS })

    const { wrapper } = await mountWithApp(NotificationPrefsView)

    expect(wrapper.findAll('[role="switch"]')).toHaveLength(7)
    expect(switchOf(wrapper, '成績公布').attributes('aria-checked')).toBe('false')
    expect(switchOf(wrapper, '作業完成').attributes('aria-checked')).toBe('true')
    expect(wrapper.findAll('.pref-row__text').map((r) => r.text())).toEqual(PREFS.map((p) => p.label))
  })

  it('NotificationPrefsView shows event icons and footnote', async () => {
    mock.onGet(URL).reply(200, { items: PREFS })

    const { wrapper } = await mountWithApp(NotificationPrefsView)

    expect(rowOf(wrapper, '作業完成').get('.pref-row__icon').text()).toBe('assignment')
    expect(rowOf(wrapper, '成績公布').get('.pref-row__icon').text()).toBe('grading')
    expect(rowOf(wrapper, '到班通知').get('.pref-row__icon').text()).toBe('how_to_reg')
    expect(rowOf(wrapper, '接送完成').get('.pref-row__icon').text()).toBe('directions_walk')
    expect(rowOf(wrapper, '到班通知').get('.pref-row__icon').attributes('aria-hidden')).toBe('true')
    expect(wrapper.text()).toContain('接送取消、綁定完成等重要通知不受此設定影響，一律會傳 LINE。')
    expect(wrapper.text()).toContain('關閉後仍會在 App 的通知中看到，只是不會傳 LINE 訊息。')
  })

  it('NotificationPrefsView toggles single event', async () => {
    mock.onGet(URL).reply(200, { items: PREFS })
    mock.onPut(URL).reply(200, {
      items: PREFS.map((p) => (p.event === 'homework.done' ? { ...p, line_enabled: false } : p)),
    })
    const { wrapper } = await mountWithApp(NotificationPrefsView)

    await switchOf(wrapper, '作業完成').trigger('click')
    // 樂觀更新：回應前就已切換
    expect(switchOf(wrapper, '作業完成').attributes('aria-checked')).toBe('false')
    await flushPromises()

    expect(mock.history.put).toHaveLength(1)
    expect(JSON.parse(mock.history.put[0]!.data as string)).toEqual({
      items: [{ event: 'homework.done', line_enabled: false }],
    })
    expect(switchOf(wrapper, '作業完成').attributes('aria-checked')).toBe('false')
    expect(switchOf(wrapper, '作業完成').attributes('aria-busy')).toBeUndefined()
  })

  it('NotificationPrefsView applies server response to all items', async () => {
    mock.onGet(URL).reply(200, { items: PREFS })
    // 回應反映伺服器的最新狀態（例如另一裝置把成績公布打開）
    mock.onPut(URL).reply(200, {
      items: PREFS.map((p) => ({ ...p, line_enabled: p.event !== 'homework.done' })),
    })
    const { wrapper } = await mountWithApp(NotificationPrefsView)

    await switchOf(wrapper, '作業完成').trigger('click')
    await flushPromises()

    expect(switchOf(wrapper, '成績公布').attributes('aria-checked')).toBe('true')
    expect(switchOf(wrapper, '作業完成').attributes('aria-checked')).toBe('false')
  })

  it('NotificationPrefsView rolls back on failure', async () => {
    mock.onGet(URL).reply(200, { items: PREFS })
    mock.onPut(URL).reply(500, SERVER_ERROR)
    const { wrapper } = await mountWithApp(NotificationPrefsView)
    const snackbar = useSnackbarStore()

    await switchOf(wrapper, '作業完成').trigger('click')
    expect(switchOf(wrapper, '作業完成').attributes('aria-checked')).toBe('false')
    await flushPromises()

    expect(switchOf(wrapper, '作業完成').attributes('aria-checked')).toBe('true')
    expect(snackbar.items.map((i) => [i.message, i.tone])).toEqual([['儲存失敗，請稍後再試', 'error']])
  })

  it('NotificationPrefsView per event lock', async () => {
    mock.onGet(URL).reply(200, { items: PREFS })
    const first = deferredReply()
    const second = deferredReply()
    mock.onPut(URL).replyOnce(first.reply).onPut(URL).replyOnce(second.reply)
    const { wrapper } = await mountWithApp(NotificationPrefsView)

    await switchOf(wrapper, '作業完成').trigger('click')
    await flushPromises()
    expect(switchOf(wrapper, '作業完成').attributes('aria-busy')).toBe('true')

    await switchOf(wrapper, '作業完成').trigger('click')
    await flushPromises()
    expect(mock.history.put).toHaveLength(1)
    expect(switchOf(wrapper, '作業完成').attributes('aria-checked')).toBe('false')

    await switchOf(wrapper, '到班通知').trigger('click')
    await flushPromises()
    expect(mock.history.put).toHaveLength(2)
    expect(JSON.parse(mock.history.put[1]!.data as string)).toEqual({
      items: [{ event: 'attendance.checked_in', line_enabled: false }],
    })

    first.resolve([200, { items: PREFS.map((p) => (p.event === 'homework.done' ? { ...p, line_enabled: false } : p)) }])
    second.resolve([500, SERVER_ERROR])
    await flushPromises()

    expect(switchOf(wrapper, '作業完成').attributes('aria-busy')).toBeUndefined()
    expect(switchOf(wrapper, '到班通知').attributes('aria-checked')).toBe('true')
  })

  it('NotificationPrefsView load error', async () => {
    mock.onGet(URL).replyOnce(500, SERVER_ERROR).onGet(URL).replyOnce(200, { items: PREFS })

    const { wrapper } = await mountWithApp(NotificationPrefsView)

    expect(wrapper.text()).toContain('通知設定載入失敗')
    expect(wrapper.get('[role="alert"]').text()).toContain('重試')
    expect(wrapper.findAll('[role="switch"]')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('關閉後仍會在 App 的通知中看到')

    const retry = wrapper.findAll('button').find((b) => b.text().includes('重試'))
    await retry!.trigger('click')
    await flushPromises()

    expect(mock.history.get).toHaveLength(2)
    expect(wrapper.findAll('[role="switch"]')).toHaveLength(7)
  })

  it('NotificationPrefsView shows skeleton while loading', async () => {
    const pending = deferredReply()
    mock.onGet(URL).reply(pending.reply)

    const { wrapper } = await mountWithApp(NotificationPrefsView)

    expect(wrapper.findAll('.skeleton-row')).toHaveLength(7)
    expect(wrapper.findAll('[role="switch"]')).toHaveLength(0)

    pending.resolve([200, { items: PREFS }])
    await flushPromises()
    expect(wrapper.findAll('.skeleton-row')).toHaveLength(0)
    expect(wrapper.findAll('[role="switch"]')).toHaveLength(7)
  })
})
