import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createWsClient, type WsClient, type WsClientOptions } from './createWsClient'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []

  readonly url: string
  readyState = 0
  sent: string[] = []
  closeCalls = 0
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
    this.closeCalls += 1
    this.readyState = 3
  }

  serverOpen(): void {
    this.readyState = 1
    this.onopen?.(new Event('open'))
  }

  serverMessage(data: string): void {
    this.onmessage?.({ data } as MessageEvent)
  }

  serverClose(code: number): void {
    this.readyState = 3
    this.onclose?.({ code, reason: '', wasClean: false } as CloseEvent)
  }
}

const sockets = FakeWebSocket.instances
const lastSocket = (): FakeWebSocket => {
  const s = sockets.at(-1)
  if (!s) throw new Error('尚未建立 socket')
  return s
}

// 每個測試結束時 stop，避免 visibilitychange 監聽殘留到下一個測試
const activeClients: WsClient[] = []

function makeClient(overrides: Partial<WsClientOptions> = {}) {
  const onPoll = vi.fn()
  const onOpen = vi.fn()
  const onAuthClose = vi.fn()
  const client = createWsClient({
    path: '/api/ws/admin',
    onPoll,
    onOpen,
    onAuthClose,
    createSocket: (url) => new FakeWebSocket(url) as unknown as WebSocket,
    ...overrides,
  })
  activeClients.push(client)
  return { client, onPoll, onOpen, onAuthClose }
}

/** 斷言下一個 socket 恰在 ms 毫秒後建立 */
function expectReconnectAfter(ms: number): void {
  const before = sockets.length
  vi.advanceTimersByTime(ms - 1)
  expect(sockets.length, `${ms - 1}ms 時不應重連`).toBe(before)
  vi.advanceTimersByTime(1)
  expect(sockets.length, `${ms}ms 時應重連`).toBe(before + 1)
}

let hidden = false

function setHidden(value: boolean): void {
  hidden = value
  document.dispatchEvent(new Event('visibilitychange'))
}

describe('createWsClient', () => {
  beforeEach(() => {
    sockets.length = 0
    hidden = false
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
    vi.stubGlobal('location', { protocol: 'http:', host: '127.0.0.1:5341' })
    vi.useFakeTimers()
  })

  afterEach(() => {
    for (const c of activeClients.splice(0)) c.stop()
    vi.useRealTimers()
    delete (document as { hidden?: boolean }).hidden
  })

  it('createWsClient builds ws url from location', () => {
    const { client } = makeClient()
    client.start()
    expect(lastSocket().url).toBe('ws://127.0.0.1:5341/api/ws/admin')
    client.stop()

    vi.stubGlobal('location', { protocol: 'https:', host: 'afterschool.example.com' })
    const { client: secure } = makeClient()
    secure.start()
    expect(lastSocket().url).toBe('wss://afterschool.example.com/api/ws/admin')
    secure.stop()
  })

  it('createWsClient dispatches envelopes by type', () => {
    const { client } = makeClient()
    const onRequest = vi.fn()
    const onHomework = vi.fn()
    const onAny = vi.fn()
    const throwing = vi.fn(() => {
      throw new Error('handler 壞掉')
    })
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    client.on('pickup.request_updated', throwing)
    client.on('pickup.request_updated', onRequest)
    client.on('homework.progress_updated', onHomework)
    client.on('*', onAny)
    client.start()
    lastSocket().serverOpen()

    const raw = '{"type":"pickup.request_updated","data":{"id":"r1"},"sent_at":"2026-10-02T08:00:00Z"}'
    lastSocket().serverMessage(raw)

    expect(onRequest).toHaveBeenCalledTimes(1)
    expect(onRequest.mock.calls[0]?.[0]).toEqual({ id: 'r1' })
    expect(onRequest.mock.calls[0]?.[1]).toEqual({
      type: 'pickup.request_updated',
      data: { id: 'r1' },
      sent_at: '2026-10-02T08:00:00Z',
    })
    expect(onHomework).toHaveBeenCalledTimes(0)
    expect(onAny).toHaveBeenCalledTimes(1)
    expect(consoleError).toHaveBeenCalledTimes(1)

    expect(() => lastSocket().serverMessage('not-json')).not.toThrow()
    expect(() => lastSocket().serverMessage('{"data":{}}')).not.toThrow()
    expect(() => lastSocket().serverMessage('null')).not.toThrow()
    expect(onAny).toHaveBeenCalledTimes(1)
  })

  it('createWsClient unsubscribe stops delivery', () => {
    const { client } = makeClient()
    const handler = vi.fn()
    const off = client.on('ready', handler)
    client.start()
    lastSocket().serverOpen()
    lastSocket().serverMessage('{"type":"ready","data":{},"sent_at":"2026-10-02T08:00:00Z"}')
    off()
    lastSocket().serverMessage('{"type":"ready","data":{},"sent_at":"2026-10-02T08:00:01Z"}')

    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('createWsClient reconnects with exponential backoff', () => {
    const { client } = makeClient()
    client.start()
    expect(client.status.value).toBe('connecting')
    lastSocket().serverOpen()
    expect(client.status.value).toBe('open')

    lastSocket().serverClose(1006)
    expect(client.status.value).toBe('reconnecting')
    expectReconnectAfter(1000)
    expect(sockets.length).toBe(2)
    expect(client.status.value).toBe('reconnecting')

    lastSocket().serverClose(1006)
    expectReconnectAfter(2000)
    expect(sockets.length).toBe(3)
    expect(client.status.value).toBe('reconnecting')

    lastSocket().serverClose(1006)
    expectReconnectAfter(4000)
    expect(sockets.length).toBe(4)
    expect(client.status.value).toBe('reconnecting')
  })

  it('createWsClient caps backoff and resets after open', () => {
    const { client } = makeClient()
    client.start()
    lastSocket().serverOpen()

    for (const delay of [1000, 2000, 4000, 8000, 16000, 30000]) {
      lastSocket().serverClose(1006)
      expectReconnectAfter(delay)
    }
    // 連續失敗 6 次後，第 7 次仍為上限
    lastSocket().serverClose(1006)
    expectReconnectAfter(30000)

    lastSocket().serverOpen()
    lastSocket().serverClose(1006)
    expectReconnectAfter(1000)
  })

  it('createWsClient polls while disconnected', () => {
    const { client, onPoll } = makeClient()
    client.start()
    lastSocket().serverOpen()
    expect(onPoll).toHaveBeenCalledTimes(0)

    lastSocket().serverClose(1006)
    expect(onPoll).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(45000)
    expect(onPoll).toHaveBeenCalledTimes(4)

    lastSocket().serverOpen()
    vi.advanceTimersByTime(60000)
    expect(onPoll).toHaveBeenCalledTimes(4)
  })

  it('createWsClient polls when the first connection fails', () => {
    const { client, onPoll } = makeClient({ pollIntervalMs: 5000 })
    client.start()
    expect(onPoll).toHaveBeenCalledTimes(0)

    lastSocket().serverClose(1006)
    expect(client.status.value).toBe('reconnecting')
    expect(onPoll).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(5000)
    expect(onPoll).toHaveBeenCalledTimes(2)
  })

  it('createWsClient stops on auth close', () => {
    for (const code of [4401, 4403]) {
      sockets.length = 0
      const { client, onPoll, onAuthClose } = makeClient()
      client.start()
      lastSocket().serverOpen()

      lastSocket().serverClose(code)

      expect(onAuthClose).toHaveBeenCalledWith(code)
      expect(client.status.value).toBe('closed')
      vi.advanceTimersByTime(60000)
      expect(sockets.length).toBe(1)
      expect(onPoll).toHaveBeenCalledTimes(0)
      // 已停止：頁面切換可見性也不重連
      setHidden(true)
      setHidden(false)
      expect(sockets.length).toBe(1)
    }
  })

  it('createWsClient stop prevents reconnect', () => {
    const { client, onPoll } = makeClient()
    client.start()
    client.start()
    expect(sockets.length).toBe(1)
    const first = lastSocket()

    client.stop()
    vi.advanceTimersByTime(60000)

    expect(sockets.length).toBe(1)
    expect(first.onclose).toBeNull()
    expect(first.onmessage).toBeNull()
    expect(first.closeCalls).toBe(1)
    expect(onPoll).toHaveBeenCalledTimes(0)

    client.start()
    expect(sockets.length).toBe(2)
  })

  it('createWsClient stop clears reconnect and poll timers', () => {
    const { client, onPoll } = makeClient()
    client.start()
    lastSocket().serverOpen()
    lastSocket().serverClose(1006)
    expect(onPoll).toHaveBeenCalledTimes(1)

    client.stop()
    vi.advanceTimersByTime(60000)

    expect(sockets.length).toBe(1)
    expect(onPoll).toHaveBeenCalledTimes(1)
  })

  it('createWsClient onOpen reports reconnect', () => {
    const { client, onOpen } = makeClient()
    client.start()
    lastSocket().serverOpen()
    expect(onOpen).toHaveBeenLastCalledWith({ isReconnect: false })

    lastSocket().serverClose(1006)
    vi.advanceTimersByTime(1000)
    lastSocket().serverOpen()

    expect(onOpen).toHaveBeenCalledTimes(2)
    expect(onOpen).toHaveBeenLastCalledWith({ isReconnect: true })
  })

  it('createWsClient send requires open socket', () => {
    const { client } = makeClient()
    expect(client.send({ action: 'subscribe' })).toBe(false)
    client.start()

    expect(client.send({ action: 'subscribe' })).toBe(false)
    expect(lastSocket().sent).toEqual([])

    lastSocket().serverOpen()
    expect(client.send({ action: 'subscribe' })).toBe(true)
    expect(lastSocket().sent[0]).toBe('{"action":"subscribe"}')
  })

  it('createWsClient pauses when page hidden', () => {
    const { client, onPoll } = makeClient()
    client.start()
    const first = lastSocket()
    first.serverOpen()

    setHidden(true)

    expect(first.closeCalls).toBe(1)
    expect(first.onclose).toBeNull()
    expect(client.status.value).toBe('idle')
    vi.advanceTimersByTime(60000)
    expect(sockets.length).toBe(1)
    expect(onPoll).toHaveBeenCalledTimes(0)

    setHidden(false)

    expect(onPoll).toHaveBeenCalledTimes(1)
    expect(sockets.length).toBe(2)
    expect(client.status.value).toBe('connecting')
  })

  it('createWsClient hidden page stops polling and pending reconnect', () => {
    const { client, onPoll } = makeClient()
    client.start()
    lastSocket().serverOpen()
    lastSocket().serverClose(1006)
    expect(onPoll).toHaveBeenCalledTimes(1)

    setHidden(true)
    vi.advanceTimersByTime(60000)

    expect(sockets.length).toBe(1)
    expect(onPoll).toHaveBeenCalledTimes(1)
    expect(client.status.value).toBe('idle')
  })
})
