import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { useAdminWsStore, type Topic } from '@/stores/adminWs'
import { useAdminWsTopic } from './useAdminWsTopic'

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

type TopicResult = ReturnType<typeof useAdminWsTopic>

let pinia: Pinia

/** 掛一個使用 composable 的最小元件，回傳 wrapper 與 composable 的回傳值 */
function mountTopic(
  topic: Topic,
  handlers: Parameters<typeof useAdminWsTopic>[1] = {},
  resync: () => void | Promise<void> = () => undefined,
): { wrapper: VueWrapper; result: TopicResult } {
  let result: TopicResult | null = null
  const Page = defineComponent({
    setup() {
      result = useAdminWsTopic(topic, handlers, { resync })
      const r = result
      return () => h('div', { class: 'page' }, r.denied.value ? 'denied' : r.status.value)
    },
  })
  const wrapper = mount(Page, { global: { plugins: [pinia] } })
  if (!result) throw new Error('setup 未執行')
  return { wrapper, result }
}

function startOpen() {
  const store = useAdminWsStore()
  store.start()
  lastSocket().serverOpen()
  return store
}

describe('useAdminWsTopic', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    sockets.length = 0
    vi.stubGlobal('WebSocket', FakeWebSocket)
    pinia = createPinia()
    setActivePinia(pinia)
  })

  afterEach(() => {
    useAdminWsStore().stop()
    vi.useRealTimers()
  })

  it('useAdminWsTopic shares subscription across components', () => {
    startOpen()
    const socket = lastSocket()

    const first = mountTopic('homework')
    const second = mountTopic('homework')

    expect(sentMessages(socket).filter((m) => m.action === 'subscribe')).toEqual([
      { action: 'subscribe', topics: ['homework'] },
    ])
    expect(useAdminWsStore().topics.homework).toBe(2)

    first.wrapper.unmount()
    expect(sentMessages(socket).some((m) => m.action === 'unsubscribe')).toBe(false)
    expect(useAdminWsStore().topics.homework).toBe(1)

    second.wrapper.unmount()
    expect(sentMessages(socket).at(-1)).toEqual({ action: 'unsubscribe', topics: ['homework'] })
    expect(useAdminWsStore().topics.homework).toBe(0)
    expect(socket.sent).toHaveLength(2)
  })

  it('useAdminWsTopic exposes connection status', () => {
    const store = useAdminWsStore()
    store.start()
    const { wrapper, result } = mountTopic('pickup')

    expect(result.status.value).toBe('connecting')
    lastSocket().serverOpen()
    expect(result.status.value).toBe('open')
    expect(wrapper.text()).toBe('open')
    // 尚未 open 時送不出去，open 時由 store 補送
    expect(sentMessages(lastSocket())).toEqual([{ action: 'subscribe', topics: ['pickup'] }])
  })

  it('useAdminWsTopic dispatches events to handlers', () => {
    startOpen()
    const progress = vi.fn()
    const other = vi.fn()
    const { wrapper } = mountTopic('homework', {
      'homework.progress_updated': progress,
      'homework.other': other,
    })

    lastSocket().serverMessage({
      type: 'homework.progress_updated',
      data: { student_id: 's1', service_date: '2026-10-07' },
      sent_at: '2026-10-07T08:00:00Z',
    })
    lastSocket().serverMessage({
      type: 'pickup.request_updated',
      data: { id: 'p1' },
      sent_at: '2026-10-07T08:00:01Z',
    })

    expect(progress).toHaveBeenCalledTimes(1)
    expect((progress.mock.calls[0]?.[0] as { student_id: string }).student_id).toBe('s1')
    expect(other).not.toHaveBeenCalled()

    wrapper.unmount()
    lastSocket().serverMessage({
      type: 'homework.progress_updated',
      data: { student_id: 's1', service_date: '2026-10-07' },
      sent_at: '2026-10-07T08:00:02Z',
    })
    expect(progress).toHaveBeenCalledTimes(1)
  })

  it('useAdminWsTopic resyncs on poll and reconnect without overlap', async () => {
    startOpen()
    const resync = vi.fn(() => new Promise<void>((resolve) => setTimeout(resolve, 500)))
    mountTopic('attendance', {}, resync)
    expect(resync).not.toHaveBeenCalled()

    // 斷線：立刻輪詢一次
    lastSocket().serverClose(1006)
    expect(resync).toHaveBeenCalledTimes(1)

    // 200ms 內再觸發輪詢（分頁切回前景）：上一輪還在跑，略過
    vi.advanceTimersByTime(100)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(resync).toHaveBeenCalledTimes(1)

    // 上一輪完成後重連成功：補抓一次
    await vi.advanceTimersByTimeAsync(500)
    expect(sockets).toHaveLength(2)
    lastSocket().serverOpen()
    expect(resync).toHaveBeenCalledTimes(2)
  })

  it('useAdminWsTopic resync failure does not block the next run', async () => {
    startOpen()
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const resync = vi
      .fn<() => void | Promise<void>>()
      .mockImplementationOnce(() => Promise.reject(new Error('載入失敗')))
      .mockImplementationOnce(() => {
        throw new Error('同步失敗')
      })
      .mockImplementation(() => undefined)
    const { wrapper } = mountTopic('attendance', {}, resync)

    lastSocket().serverClose(1006)
    expect(resync).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(15000)
    expect(resync).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(15000)
    expect(resync).toHaveBeenCalledTimes(3)

    // 卸載後不再輪詢、也不在重連時補抓
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(15000)
    lastSocket().serverOpen()
    expect(resync).toHaveBeenCalledTimes(3)
  })

  it('useAdminWsTopic exposes denied flag', () => {
    startOpen()
    const attendance = mountTopic('attendance')
    const pickup = mountTopic('pickup')
    expect(attendance.result.denied.value).toBe(false)

    lastSocket().serverMessage({ type: 'error', code: 'permission_denied', topics: ['attendance'] })

    expect(attendance.result.denied.value).toBe(true)
    expect(attendance.wrapper.text()).toBe('denied')
    expect(pickup.result.denied.value).toBe(false)
    expect(pickup.wrapper.text()).toBe('open')
  })
})
