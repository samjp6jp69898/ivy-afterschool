// FRONTEND-004：WebSocket 用戶端（後台 /api/ws/admin 與家長端 /api/ws/parent 共用）。
// 移植 ivy FE:src/composables/useInboxNotifications.ts 的重連排程與 visibilitychange 處理、
// FE:src/utils/ws.ts::closeWebSocketSafely（先卸 handler 再 close，防殭屍重連）、
// FE:src/views/DismissalQueueView.vue 斷線時以 HTTP 輪詢補齊。
// 推播為盡力而為：非 open 期間以 onPoll 輪詢對應 REST endpoint 補齊。
//
// status：idle（未啟動 / stop / 頁面隱藏）→ connecting → open；非主動斷線 → reconnecting（直到下次 open）；
// 4401 / 4403 → closed（停止重連與輪詢，需再 start）。
import { shallowRef, type ShallowRef } from 'vue'
import type { WsEnvelope } from '@/shared/types/api'

export type WsStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface WsClientOptions {
  /** '/api/ws/admin'；依 location.protocol 組 ws:// 或 wss:// + location.host */
  path: string
  /** 每次連線成功（用來重送 subscribe、重新抓資料） */
  onOpen?: (ctx: { isReconnect: boolean }) => void
  /** 非 open 狀態期間的補齊輪詢 */
  onPoll?: () => void
  pollIntervalMs?: number
  initialDelayMs?: number
  maxDelayMs?: number
  /** close code 4401 / 4403：停止重連 */
  onAuthClose?: (code: number) => void
  /** 測試注入，預設 new WebSocket(url) */
  createSocket?: (url: string) => WebSocket
}

export type WsHandler<T = unknown> = (data: T, envelope: WsEnvelope<T>) => void

export interface WsClient {
  readonly status: Readonly<ShallowRef<WsStatus>>
  start(): void
  stop(): void
  /** 非 OPEN 時回 false 且不送 */
  send(message: object): boolean
  /** 回傳取消函式；type '*' 收全部 */
  on<T = unknown>(type: string, handler: WsHandler<T>): () => void
}

const AUTH_CLOSE_CODES = new Set([4401, 4403])
// 不依賴全域 WebSocket 常數（測試環境可能沒有）
const READY_STATE_OPEN = 1

function closeSocketSafely(socket: WebSocket | null): void {
  if (!socket) return
  socket.onopen = null
  socket.onmessage = null
  socket.onclose = null
  socket.onerror = null
  try {
    socket.close()
  } catch {
    // 已關閉或尚未建立完成的 socket，忽略
  }
}

function parseEnvelope(raw: unknown): WsEnvelope | null {
  if (typeof raw !== 'string') return null
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (parsed === null || typeof parsed !== 'object') return null
  const env = parsed as Partial<WsEnvelope>
  return typeof env.type === 'string' ? (env as WsEnvelope) : null
}

export function createWsClient(opts: WsClientOptions): WsClient {
  const pollIntervalMs = opts.pollIntervalMs ?? 15000
  const initialDelayMs = opts.initialDelayMs ?? 1000
  const maxDelayMs = opts.maxDelayMs ?? 30000
  const createSocket = opts.createSocket ?? ((url: string) => new WebSocket(url))

  const status = shallowRef<WsStatus>('idle')
  const handlers = new Map<string, Set<WsHandler>>()

  let started = false
  let socket: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let attempt = 0
  let hasOpened = false

  function isHidden(): boolean {
    return typeof document !== 'undefined' && document.hidden === true
  }

  function buildUrl(): string {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${location.host}${opts.path}`
  }

  function invokePoll(): void {
    try {
      void Promise.resolve(opts.onPoll?.()).catch(() => undefined)
    } catch {
      // 輪詢失敗不可中斷重連生命週期，下一輪會再補
    }
  }

  function startPolling(): void {
    if (pollTimer !== null) return
    invokePoll()
    pollTimer = setInterval(invokePoll, pollIntervalMs)
  }

  function stopPolling(): void {
    if (pollTimer === null) return
    clearInterval(pollTimer)
    pollTimer = null
  }

  function clearReconnectTimer(): void {
    if (reconnectTimer === null) return
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }

  function dropSocket(): void {
    const current = socket
    socket = null
    closeSocketSafely(current)
  }

  function dispatch(env: WsEnvelope): void {
    const targets = [...(handlers.get(env.type) ?? []), ...(handlers.get('*') ?? [])]
    for (const handler of targets) {
      try {
        handler(env.data, env)
      } catch (err) {
        // 單一 handler 失敗不影響其他 handler
        console.error('[ws] handler 執行失敗', env.type, err)
      }
    }
  }

  function scheduleReconnect(): void {
    if (!started || reconnectTimer !== null || isHidden()) return
    const delay = Math.min(initialDelayMs * 2 ** attempt, maxDelayMs)
    attempt += 1
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
  }

  function handleClose(code: number): void {
    socket = null
    if (AUTH_CLOSE_CODES.has(code)) {
      teardown('closed')
      opts.onAuthClose?.(code)
      return
    }
    status.value = 'reconnecting'
    startPolling()
    scheduleReconnect()
  }

  function connect(): void {
    if (!started || socket !== null || isHidden()) return
    if (status.value !== 'reconnecting') status.value = 'connecting'

    const current = createSocket(buildUrl())
    socket = current

    current.onopen = () => {
      if (socket !== current) return
      attempt = 0
      clearReconnectTimer()
      stopPolling()
      status.value = 'open'
      const isReconnect = hasOpened
      hasOpened = true
      opts.onOpen?.({ isReconnect })
    }
    current.onmessage = (event: MessageEvent) => {
      if (socket !== current) return
      const env = parseEnvelope(event.data)
      if (env) dispatch(env)
    }
    current.onclose = (event: CloseEvent) => {
      if (socket !== current) return
      handleClose(event.code)
    }
    // error 之後瀏覽器一定會再送 close，重連交給 onclose 處理
    current.onerror = null
  }

  function onVisibilityChange(): void {
    if (!started) return
    if (isHidden()) {
      clearReconnectTimer()
      stopPolling()
      dropSocket()
      status.value = 'idle'
      return
    }
    attempt = 0
    invokePoll()
    connect()
  }

  /** 停止一切活動：計時器、socket、可見性監聽 */
  function teardown(next: WsStatus): void {
    started = false
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
    clearReconnectTimer()
    stopPolling()
    dropSocket()
    status.value = next
  }

  function start(): void {
    if (started) return
    started = true
    attempt = 0
    hasOpened = false
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibilityChange)
    }
    connect()
  }

  function stop(): void {
    if (!started) return
    teardown('idle')
  }

  function send(message: object): boolean {
    if (!socket || socket.readyState !== READY_STATE_OPEN) return false
    socket.send(JSON.stringify(message))
    return true
  }

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

  return { status, start, stop, send, on }
}
