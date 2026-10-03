import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * 家長端全域 Snackbar 佇列。畫面一次只顯示 items[0]（M3Snackbar 渲染、ParentLayout 掛載），
 * 第一則顯示時才開始計時，到時自動 dismiss 並輪到下一則。
 */

export interface SnackbarAction {
  label: string
  onClick: () => void
}

export type SnackbarTone = 'info' | 'error'

export interface SnackbarItem {
  id: number
  message: string
  tone: SnackbarTone
  /** 毫秒；0 代表不自動消失 */
  duration: number
  action?: SnackbarAction
}

export interface SnackbarOptions {
  tone?: SnackbarTone
  duration?: number
  action?: SnackbarAction
}

const DEFAULT_DURATION_MS = 4000
const MAX_ITEMS = 3

export const useSnackbarStore = defineStore('parentSnackbar', () => {
  const items = ref<SnackbarItem[]>([])
  let nextId = 1
  let timer: ReturnType<typeof setTimeout> | null = null

  function stopTimer(): void {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  function startHeadTimer(): void {
    stopTimer()
    const head = items.value[0]
    if (!head || head.duration <= 0) return
    const headId = head.id
    timer = setTimeout(() => {
      timer = null
      dismiss(headId)
    }, head.duration)
  }

  function show(message: string, opts: SnackbarOptions = {}): number {
    const item: SnackbarItem = {
      id: nextId++,
      message,
      tone: opts.tone ?? 'info',
      duration: opts.duration ?? DEFAULT_DURATION_MS,
    }
    if (opts.action) item.action = opts.action
    items.value.push(item)
    // 超過上限時丟棄最舊的未顯示項目；正在顯示的 items[0] 保留
    if (items.value.length > MAX_ITEMS) items.value.splice(1, 1)
    if (items.value.length === 1) startHeadTimer()
    return item.id
  }

  function dismiss(id: number): void {
    const index = items.value.findIndex((i) => i.id === id)
    if (index === -1) return
    items.value.splice(index, 1)
    if (index === 0) startHeadTimer()
  }

  function clear(): void {
    stopTimer()
    items.value = []
  }

  return { items, show, dismiss, clear }
})
