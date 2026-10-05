// FRONTEND-037：整個後台共用一條 /api/ws/admin 連線（BACKEND-226）。
// 移植 ivy FE:src/views/DismissalQueueView.vue::connectWs 的訂閱與斷線輪詢，改為全站單一連線：
// topic 以引用計數訂閱（多個頁面 / 元件共用），重連後自動重新訂閱；斷線期間呼叫各頁註冊的 poller 補齊，
// 重連後呼叫 reconnect listener 讓各頁重新抓資料。
// 事件 handler 由 store 自己保存，stop / start 換 client 後仍有效。
import { ElMessage } from 'element-plus'
import { defineStore } from 'pinia'
import { computed, reactive, ref, shallowRef } from 'vue'
import { triggerAuthFailure } from '@/api/http'
import { createWsClient, type WsClient, type WsHandler, type WsStatus } from '@/shared/ws/createWsClient'
import type { WsEnvelope } from '@/shared/types/api'

export const WS_TOPICS = ['pickup', 'homework', 'attendance'] as const
export type Topic = (typeof WS_TOPICS)[number]

const WS_PATH = '/api/ws/admin'

/** 控制訊息沒有 data，欄位在信封頂層 */
interface ControlMessage {
  type: string
  code?: string
  reason?: string
  topics?: string[]
}

function isTopic(v: unknown): v is Topic {
  return typeof v === 'string' && (WS_TOPICS as readonly string[]).includes(v)
}

function controlTopics(env: WsEnvelope): Topic[] {
  const topics = (env as unknown as ControlMessage).topics
  return Array.isArray(topics) ? topics.filter(isTopic) : []
}

/** 依序呼叫；單一 callback 失敗（含 rejected promise）不影響其他 */
function runAll(fns: Iterable<() => unknown>): void {
  for (const fn of fns) {
    try {
      void Promise.resolve(fn()).catch(() => undefined)
    } catch {
      // 忽略，下一個繼續
    }
  }
}

export const useAdminWsStore = defineStore('adminWs', () => {
  const client = shallowRef<WsClient | null>(null)
  const status = computed<WsStatus>(() => client.value?.status.value ?? 'idle')
  const topics = reactive<Record<Topic, number>>({ pickup: 0, homework: 0, attendance: 0 })
  const deniedTopics = ref<Topic[]>([])

  const handlers = new Map<string, Set<WsHandler>>()
  const pollers = new Set<() => unknown>()
  const reconnectListeners = new Set<() => unknown>()

  function activeTopics(): Topic[] {
    return WS_TOPICS.filter((t) => topics[t] > 0)
  }

  function dispatch(data: unknown, env: WsEnvelope): void {
    const targets = [...(handlers.get(env.type) ?? []), ...(handlers.get('*') ?? [])]
    for (const handler of targets) {
      try {
        handler(data, env)
      } catch (err) {
        console.error('[adminWs] handler 執行失敗', env.type, err)
      }
    }
  }

  function onControlError(_: unknown, env: WsEnvelope): void {
    if ((env as unknown as ControlMessage).code !== 'permission_denied') return
    const next = new Set(deniedTopics.value)
    for (const t of controlTopics(env)) next.add(t)
    deniedTopics.value = WS_TOPICS.filter((t) => next.has(t))
  }

  function onControlUnsubscribed(_: unknown, env: WsEnvelope): void {
    if ((env as unknown as ControlMessage).reason !== 'permission_revoked') return
    for (const t of controlTopics(env)) topics[t] = 0
    ElMessage.warning('權限已變更，部分即時更新已停止')
  }

  function onOpen({ isReconnect }: { isReconnect: boolean }): void {
    const current = activeTopics()
    if (current.length) client.value?.send({ action: 'subscribe', topics: current })
    if (isReconnect) runAll(reconnectListeners)
  }

  function start(): void {
    if (client.value) return
    const c = createWsClient({
      path: WS_PATH,
      onOpen,
      onPoll: () => runAll(pollers),
      onAuthClose: () => triggerAuthFailure(),
    })
    c.on('*', dispatch)
    c.on('error', onControlError)
    c.on('unsubscribed', onControlUnsubscribed)
    client.value = c
    c.start()
  }

  function stop(): void {
    client.value?.stop()
    client.value = null
    for (const t of WS_TOPICS) topics[t] = 0
    deniedTopics.value = []
  }

  function subscribe(topic: Topic): void {
    topics[topic] += 1
    // 尚未 open 時 send 回 false，交給 onOpen 一次送出
    if (topics[topic] === 1) client.value?.send({ action: 'subscribe', topics: [topic] })
  }

  function unsubscribe(topic: Topic): void {
    if (topics[topic] === 0) return
    topics[topic] -= 1
    if (topics[topic] === 0) client.value?.send({ action: 'unsubscribe', topics: [topic] })
  }

  /** type '*' 收全部；回傳解除函式 */
  function on<T = unknown>(type: string, handler: WsHandler<T>): () => void {
    let set = handlers.get(type)
    if (!set) {
      set = new Set()
      handlers.set(type, set)
    }
    const h = handler as WsHandler
    set.add(h)
    return () => {
      handlers.get(type)?.delete(h)
    }
  }

  function registerPoller(fn: () => unknown): () => void {
    pollers.add(fn)
    return () => {
      pollers.delete(fn)
    }
  }

  function onReconnect(fn: () => unknown): () => void {
    reconnectListeners.add(fn)
    return () => {
      reconnectListeners.delete(fn)
    }
  }

  return {
    status,
    topics,
    deniedTopics,
    start,
    stop,
    subscribe,
    unsubscribe,
    on,
    registerPoller,
    onReconnect,
  }
})
