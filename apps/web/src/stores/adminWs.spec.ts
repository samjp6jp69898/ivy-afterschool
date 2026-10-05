import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetAdminHttpHandlers, setAdminHttpHandlers } from '@/api/http'
import { useAdminWsStore } from './adminWs'

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

  serverClose(code: number): void {
    this.readyState = 3
    this.onclose?.({ code, reason: '', wasClean: false } as CloseEvent)
  }
}

const sockets = FakeWebSocket.instances

function lastSocket(): FakeWebSocket {
  const s = sockets.at(-1)
  if (!s) throw new Error('尚未建立 socket')
  return s
}

function sentMessages(socket: FakeWebSocket): { action: string; topics: string[] }[] {
  return socket.sent.map((s) => JSON.parse(s) as { action: string; topics: string[] })
}

function startOpen() {
  const store = useAdminWsStore()
  store.start()
  lastSocket().serverOpen()
  return store
}

describe('adminWs store', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    sockets.length = 0
    vi.stubGlobal('WebSocket', FakeWebSocket)
    setActivePinia(createPinia())
  })

  afterEach(() => {
    useAdminWsStore().stop()
    resetAdminHttpHandlers()
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('adminWs store connects to admin ws path once', () => {
    const store = useAdminWsStore()
    store.start()
    store.start()

    expect(sockets).toHaveLength(1)
    expect(lastSocket().url).toBe(`ws://${location.host}/api/ws/admin`)
    expect(store.status).toBe('connecting')
    lastSocket().serverOpen()
    expect(store.status).toBe('open')
  })

  it('adminWs store subscribes once per topic with ref counting', () => {
    const store = startOpen()
    const socket = lastSocket()

    store.subscribe('pickup')
    store.subscribe('pickup')

    expect(socket.sent).toEqual(['{"action":"subscribe","topics":["pickup"]}'])
    expect(store.topics.pickup).toBe(2)

    store.unsubscribe('pickup')
    expect(socket.sent).toHaveLength(1)
    store.unsubscribe('pickup')
    expect(socket.sent[1]).toBe('{"action":"unsubscribe","topics":["pickup"]}')
    expect(store.topics.pickup).toBe(0)

    // 計數已為 0 時不再送、不變負
    store.unsubscribe('pickup')
    expect(socket.sent).toHaveLength(2)
    expect(store.topics.pickup).toBe(0)
  })

  it('adminWs store resubscribes all topics after reconnect', () => {
    const store = startOpen()
    store.subscribe('pickup')
    store.subscribe('homework')

    lastSocket().serverClose(1006)
    expect(store.status).toBe('reconnecting')
    vi.advanceTimersByTime(1000)
    expect(sockets).toHaveLength(2)
    lastSocket().serverOpen()

    const first = sentMessages(lastSocket())[0]!
    expect(first.action).toBe('subscribe')
    expect(new Set(first.topics)).toEqual(new Set(['pickup', 'homework']))
    expect(lastSocket().sent).toHaveLength(1)
  })

  it('adminWs store queues subscribe before open', () => {
    const store = useAdminWsStore()
    store.start()
    store.subscribe('attendance')

    expect(lastSocket().sent).toEqual([])
    lastSocket().serverOpen()

    expect(sentMessages(lastSocket())[0]).toEqual({ action: 'subscribe', topics: ['attendance'] })
  })

  it('adminWs store sends nothing on open without topics', () => {
    startOpen()

    expect(lastSocket().sent).toEqual([])
  })

  it('adminWs store runs pollers while disconnected and reconnect listeners after reopen', () => {
    const store = startOpen()
    const poller = vi.fn()
    const failing = vi.fn(() => {
      throw new Error('網路錯誤')
    })
    const reconnect = vi.fn()
    store.registerPoller(failing)
    store.registerPoller(poller)
    const offReconnect = store.onReconnect(reconnect)

    lastSocket().serverClose(1006)
    expect(poller).toHaveBeenCalledTimes(1)
    expect(failing).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(15000)
    expect(poller).toHaveBeenCalledTimes(2)
    expect(reconnect).not.toHaveBeenCalled()

    lastSocket().serverOpen()
    expect(reconnect).toHaveBeenCalledTimes(1)

    offReconnect()
    lastSocket().serverClose(1006)
    vi.advanceTimersByTime(1000)
    lastSocket().serverOpen()
    expect(reconnect).toHaveBeenCalledTimes(1)
  })

  it('adminWs store unregisters pollers', () => {
    const store = startOpen()
    const poller = vi.fn()
    const off = store.registerPoller(poller)
    off()

    lastSocket().serverClose(1006)

    expect(poller).not.toHaveBeenCalled()
  })

  it('adminWs store forwards events to handlers across restarts', () => {
    const store = useAdminWsStore()
    const handler = vi.fn()
    const off = store.on('pickup.request_updated', handler)
    store.start()
    lastSocket().serverOpen()

    lastSocket().serverMessage({ type: 'pickup.request_updated', data: { id: 'p1' }, sent_at: '2026-10-02T08:00:00Z' })
    expect(handler).toHaveBeenCalledTimes(1)
    expect(handler.mock.calls[0]?.[0]).toEqual({ id: 'p1' })

    store.stop()
    store.start()
    lastSocket().serverOpen()
    lastSocket().serverMessage({ type: 'pickup.request_updated', data: { id: 'p2' }, sent_at: '2026-10-02T08:01:00Z' })
    expect(handler).toHaveBeenCalledTimes(2)

    off()
    lastSocket().serverMessage({ type: 'pickup.request_updated', data: { id: 'p3' }, sent_at: '2026-10-02T08:02:00Z' })
    expect(handler).toHaveBeenCalledTimes(2)
  })

  it('adminWs store tracks denied and revoked topics', () => {
    const store = startOpen()
    store.subscribe('pickup')
    store.subscribe('attendance')

    lastSocket().serverMessage({ type: 'error', code: 'permission_denied', topics: ['attendance'] })
    expect(store.deniedTopics).toEqual(['attendance'])
    lastSocket().serverMessage({ type: 'error', code: 'permission_denied', topics: ['attendance'] })
    expect(store.deniedTopics).toEqual(['attendance'])

    lastSocket().serverMessage({ type: 'unsubscribed', topics: ['pickup'], reason: 'permission_revoked' })
    expect(store.topics.pickup).toBe(0)
    expect(store.topics.attendance).toBe(1)
    expect(document.body.textContent).toContain('權限已變更，部分即時更新已停止')

    // 頁面離開時的 unsubscribe 不再送出
    const sentBefore = lastSocket().sent.length
    store.unsubscribe('pickup')
    expect(lastSocket().sent).toHaveLength(sentBefore)
  })

  it('adminWs store stop resets counts and denied topics', () => {
    const store = startOpen()
    store.subscribe('pickup')
    lastSocket().serverMessage({ type: 'error', code: 'permission_denied', topics: ['homework'] })

    store.stop()

    expect(store.status).toBe('idle')
    expect(store.topics).toEqual({ pickup: 0, homework: 0, attendance: 0 })
    expect(store.deniedTopics).toEqual([])
  })

  it('adminWs store auth close triggers auth failure handler', () => {
    const authFailure = vi.fn()
    setAdminHttpHandlers({ authFailure })
    const store = startOpen()

    lastSocket().serverClose(4401)

    expect(authFailure).toHaveBeenCalledTimes(1)
    expect(store.status).toBe('closed')
    vi.advanceTimersByTime(60000)
    expect(sockets).toHaveLength(1)
  })
})
