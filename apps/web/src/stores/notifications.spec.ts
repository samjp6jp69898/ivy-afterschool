import type MockAdapter from 'axios-mock-adapter'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { adminHttp } from '@/api/http'
import { ApiError, type Notification } from '@/shared/types/api'
import { useAdminWsStore } from '@/stores/adminWs'
import { createApiMock } from '@/test/helpers'
import { useNotificationsStore } from './notifications'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []

  readonly url: string
  readyState = 0
  sent: string[] = []
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    this.readyState = 3
  }

  serverOpen(): void {
    this.readyState = 1
    this.onopen?.(new Event('open'))
  }

  serverMessage(message: object): void {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent)
  }
}

function lastSocket(): FakeWebSocket {
  const s = FakeWebSocket.instances.at(-1)
  if (!s) throw new Error('尚未建立 socket')
  return s
}

function makeNotification(id: string, overrides: Partial<Notification> = {}): Notification {
  return {
    id,
    event: 'pickup.requested',
    title: '王小明家長發起接送',
    body: '預計 17:30 抵達',
    payload: { request_id: 'r1' },
    read_at: null,
    created_at: '2026-10-07T08:00:00Z',
    deep_link: '/pickup',
    ...overrides,
  }
}

const SERVER_ERROR = { error: { code: 'internal_error', message: '伺服器錯誤', details: null } }

/** 開好一條 admin ws 連線並綁定通知 store */
function startWsAndBind() {
  const store = useNotificationsStore()
  useAdminWsStore().start()
  lastSocket().serverOpen()
  store.bindWs()
  return store
}

describe('notifications store', () => {
  let mock: MockAdapter

  beforeEach(() => {
    FakeWebSocket.instances.length = 0
    vi.stubGlobal('WebSocket', FakeWebSocket)
    setActivePinia(createPinia())
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    useAdminWsStore().stop()
    mock.restore()
    document.body.innerHTML = ''
  })

  it('notifications store fetch fills items and unread count', async () => {
    mock.onGet('/admin/notifications').reply(200, {
      items: [makeNotification('n2'), makeNotification('n1', { read_at: '2026-10-07T07:00:00Z' })],
      total: 2,
      unread_count: 1,
    })
    const store = useNotificationsStore()
    expect(store.loaded).toBe(false)

    const first = store.fetch()
    expect(store.loading).toBe(true)
    await Promise.all([first, store.fetch()])

    expect(store.items.map((n) => n.id)).toEqual(['n2', 'n1'])
    expect(store.unreadCount).toBe(1)
    expect(store.total).toBe(2)
    expect(store.loaded).toBe(true)
    expect(store.loading).toBe(false)
    expect(mock.history.get).toHaveLength(1)
    expect(mock.history.get[0]?.params).toEqual({ page_size: 20 })

    // 完成後再 fetch 會重新打；unreadOnly 走 unread_only 參數
    mock.onGet('/admin/notifications').reply(200, { items: [makeNotification('n2')], total: 1, unread_count: 1 })
    await store.fetch({ unreadOnly: true })
    expect(mock.history.get).toHaveLength(2)
    expect(mock.history.get[1]?.params).toEqual({ unread_only: true, page_size: 20 })
    expect(store.items.map((n) => n.id)).toEqual(['n2'])
    expect(store.total).toBe(1)
  })

  it('notifications store fetch failure rejects and keeps unloaded', async () => {
    mock.onGet('/admin/notifications').replyOnce(500, SERVER_ERROR)
    const store = useNotificationsStore()

    const err = await store.fetch().catch((e: unknown) => e)

    expect(err).toBeInstanceOf(ApiError)
    expect(store.loaded).toBe(false)
    expect(store.loading).toBe(false)
    expect(store.items).toEqual([])

    // 失敗後 in-flight 已清除，下一次會再打
    mock.onGet('/admin/notifications').replyOnce(200, { items: [makeNotification('n1')], total: 1, unread_count: 1 })
    await store.fetch()
    expect(store.loaded).toBe(true)
    expect(store.items).toHaveLength(1)
    expect(mock.history.get).toHaveLength(2)
  })

  it('notifications store markRead is optimistic and reverts on failure', async () => {
    mock.onGet('/admin/notifications').reply(200, { items: [makeNotification('n1')], total: 1, unread_count: 1 })
    const store = useNotificationsStore()
    await store.fetch()
    expect(store.items[0]?.read_at).toBeNull()
    expect(store.unreadCount).toBe(1)

    mock.onPost('/admin/notifications/n1/read').replyOnce(500, SERVER_ERROR)
    const failing = store.markRead('n1')
    // 樂觀更新：還沒等到回應就先標已讀
    expect(store.items[0]?.read_at).not.toBeNull()
    expect(store.unreadCount).toBe(0)

    const err = await failing.catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(store.items[0]?.read_at).toBeNull()
    expect(store.unreadCount).toBe(1)

    mock.onPost('/admin/notifications/n1/read').replyOnce(200, {
      ...makeNotification('n1'),
      read_at: '2026-10-07T08:05:00Z',
    })
    await store.markRead('n1')
    expect(store.items[0]?.read_at).toBe('2026-10-07T08:05:00Z')
    expect(store.unreadCount).toBe(0)

    // 已讀的不重送
    await store.markRead('n1')
    expect(mock.history.post).toHaveLength(2)
  })

  it('notifications store markRead unread count never goes negative', async () => {
    mock.onGet('/admin/notifications').reply(200, { items: [makeNotification('n1')], total: 1, unread_count: 0 })
    mock.onPost('/admin/notifications/n1/read').reply(200, { ...makeNotification('n1'), read_at: '2026-10-07T08:05:00Z' })
    const store = useNotificationsStore()
    await store.fetch()

    await store.markRead('n1')

    expect(store.unreadCount).toBe(0)
    expect(store.items[0]?.read_at).toBe('2026-10-07T08:05:00Z')
  })

  it('notifications store receive dedupes ws notifications', () => {
    const store = useNotificationsStore()
    store.receive(makeNotification('n1'))
    expect(store.unreadCount).toBe(1)
    expect(store.total).toBe(1)

    store.receive(makeNotification('n9', { title: '林小華家長發起接送' }))
    store.receive(makeNotification('n9', { title: '林小華家長發起接送' }))

    expect(store.items[0]?.id).toBe('n9')
    expect(store.items.filter((n) => n.id === 'n9')).toHaveLength(1)
    expect(store.items.map((n) => n.id)).toEqual(['n9', 'n1'])
    expect(store.unreadCount).toBe(2)
    expect(store.total).toBe(2)

    // 已讀的通知不增加未讀數
    store.receive(makeNotification('n10', { read_at: '2026-10-07T08:00:00Z' }))
    expect(store.unreadCount).toBe(2)
    expect(store.total).toBe(3)
  })

  it('notifications store keeps at most 50 items newest first', () => {
    const store = useNotificationsStore()
    for (let i = 1; i <= 55; i += 1) store.receive(makeNotification(`n${i}`))

    expect(store.items).toHaveLength(50)
    expect(store.items[0]?.id).toBe('n55')
    expect(store.items.at(-1)?.id).toBe('n6')
    expect(store.unreadCount).toBe(55)
    expect(store.total).toBe(55)
  })

  it('notifications store binds ws once', () => {
    const store = startWsAndBind()
    const off = store.bindWs()
    const transient = vi.fn()
    store.onTransient(transient)

    lastSocket().serverMessage({ type: 'notification.created', data: makeNotification('n1'), sent_at: '2026-10-07T08:00:00Z' })

    expect(store.items).toHaveLength(1)
    expect(store.items[0]?.id).toBe('n1')
    expect(store.unreadCount).toBe(1)
    expect(store.total).toBe(1)

    // transient 沒有 id 去重：綁了兩次會收到兩次，這裡確認只有一次
    lastSocket().serverMessage({
      type: 'notification.transient',
      data: { event: 'pickup.arrived', title: '王小明家長已到', body: '', payload: { request_id: 'r1' } },
      sent_at: '2026-10-07T08:00:30Z',
    })
    expect(transient).toHaveBeenCalledTimes(1)

    // 解除後不再收；再綁一次恢復
    off()
    lastSocket().serverMessage({ type: 'notification.created', data: makeNotification('n2'), sent_at: '2026-10-07T08:01:00Z' })
    expect(store.items).toHaveLength(1)

    store.bindWs()
    lastSocket().serverMessage({ type: 'notification.created', data: makeNotification('n3'), sent_at: '2026-10-07T08:02:00Z' })
    expect(store.items.map((n) => n.id)).toEqual(['n3', 'n1'])
  })

  it('notifications store transient listeners receive pickup arrived', async () => {
    const store = startWsAndBind()
    const listener = vi.fn()
    const failing = vi.fn(() => {
      throw new Error('提示音播放失敗')
    })
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    store.onTransient(failing)
    const off = store.onTransient(listener)

    lastSocket().serverMessage({
      type: 'notification.transient',
      data: { event: 'pickup.arrived', title: '王小明家長已到', body: '', payload: { request_id: 'r1' } },
      sent_at: '2026-10-07T08:00:00Z',
    })
    await nextTick()

    expect(listener).toHaveBeenCalledTimes(1)
    expect(listener.mock.calls[0]?.[0]).toEqual({
      event: 'pickup.arrived',
      title: '王小明家長已到',
      body: '',
      payload: { request_id: 'r1' },
    })
    expect(failing).toHaveBeenCalledTimes(1)
    expect(document.body.textContent).toContain('王小明家長已到')
    // 暫態訊息不進收件匣
    expect(store.items).toEqual([])
    expect(store.unreadCount).toBe(0)

    off()
    lastSocket().serverMessage({
      type: 'notification.transient',
      data: { event: 'pickup.arrived', title: '林小華家長已到', body: '', payload: { request_id: 'r2' } },
      sent_at: '2026-10-07T08:01:00Z',
    })
    expect(listener).toHaveBeenCalledTimes(1)
    expect(failing).toHaveBeenCalledTimes(2)
  })

  it('notifications store markAllRead', async () => {
    mock.onGet('/admin/notifications').reply(200, {
      items: [makeNotification('n2'), makeNotification('n1')],
      total: 2,
      unread_count: 2,
    })
    const store = useNotificationsStore()
    await store.fetch()

    mock.onPost('/admin/notifications/read-all').replyOnce(500, SERVER_ERROR)
    const err = await store.markAllRead().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(store.items.every((n) => n.read_at === null)).toBe(true)
    expect(store.unreadCount).toBe(2)

    mock.onPost('/admin/notifications/read-all').replyOnce(200, { updated: 2 })
    const result = await store.markAllRead()

    expect(result).toEqual({ updated: 2 })
    expect(store.items.every((n) => n.read_at !== null)).toBe(true)
    expect(store.unreadCount).toBe(0)
    expect(mock.history.post.map((c) => c.url)).toEqual([
      '/admin/notifications/read-all',
      '/admin/notifications/read-all',
    ])
  })
})
