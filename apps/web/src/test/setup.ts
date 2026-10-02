// INFRA-012：vitest 全域 setup（vitest.config.ts 的 setupFiles）。
// 移植自 ivy tests/setup.js 的 installHarnessGlobals：封鎖未 mock 的網路、隔離 storage、自動 unmount。
// 每個測試的 beforeEach 重新安裝；測試自己的 beforeEach 在這之後執行，可覆蓋（例如換成 fetch mock）。
import { enableAutoUnmount } from '@vue/test-utils'
import { afterEach, beforeEach, vi } from 'vitest'

export const UNMOCKED_FETCH_MESSAGE = 'Unexpected unmocked fetch in Vitest'
export const UNMOCKED_XHR_MESSAGE = 'Unexpected unmocked XMLHttpRequest in Vitest'

// axios 在 happy-dom 走 XHR adapter：建構即 throw，沒用 axios-mock-adapter 的請求確定失敗、不會開連線
class BlockedXMLHttpRequest {
  constructor() {
    throw new Error(UNMOCKED_XHR_MESSAGE)
  }
}

// 記憶體版 Storage：Node 內建的 localStorage 與 happy-dom 的實作會互相干擾，一律自備
class MemoryStorage implements Storage {
  private store = new Map<string, string>()

  get length(): number {
    return this.store.size
  }

  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null
  }

  getItem(key: string): string | null {
    return this.store.get(String(key)) ?? null
  }

  setItem(key: string, value: string): void {
    this.store.set(String(key), String(value))
  }

  removeItem(key: string): void {
    this.store.delete(String(key))
  }

  clear(): void {
    this.store.clear()
  }
}

function installHarnessGlobals(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.reject(new Error(UNMOCKED_FETCH_MESSAGE))),
  )
  vi.stubGlobal('XMLHttpRequest', BlockedXMLHttpRequest)
  vi.stubGlobal('localStorage', new MemoryStorage())
  vi.stubGlobal('sessionStorage', new MemoryStorage())
}

installHarnessGlobals()

// setup 檔的 hook 先於測試檔的 hook 註冊：beforeEach 先重裝 harness，測試自己的 beforeEach 再覆蓋。
// 每次都換全新的 MemoryStorage，上一個測試寫入的 key 不會留到下一個。
beforeEach(() => {
  installHarnessGlobals()
})

// afterEach 依註冊順序倒著跑（sequence.hooks 預設 stack）：後註冊的 unmount 先執行，
// 元件卸載完才 unstubAllGlobals，避免卸載期間碰到已還原的真實 fetch / storage。
afterEach(() => {
  vi.unstubAllGlobals()
})

enableAutoUnmount(afterEach)
