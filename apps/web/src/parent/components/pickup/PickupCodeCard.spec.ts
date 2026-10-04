import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSnackbarStore } from '../../stores/snackbar'
import PickupCodeCard from './PickupCodeCard.vue'

const CODE = '048213'

let wrapper: VueWrapper | null = null

function mountCard(): VueWrapper {
  const pinia = createPinia()
  setActivePinia(pinia)
  wrapper = mount(PickupCodeCard, {
    props: { code: CODE, proxyName: '陳叔叔', serviceDate: '2026-10-03' },
    global: { plugins: [pinia] },
  })
  return wrapper
}

function stubClipboard(writeText: ((text: string) => Promise<void>) | undefined): void {
  Object.defineProperty(navigator, 'clipboard', {
    value: writeText ? { writeText } : undefined,
    configurable: true,
  })
}

function copyButton(w: VueWrapper) {
  const found = w.findAll('button').find((b) => b.text().includes('複製接送碼') || b.text().includes('已複製'))
  if (!found) throw new Error('找不到複製按鈕')
  return found
}

function messages(): string[] {
  return useSnackbarStore().items.map((i) => i.message)
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
  vi.restoreAllMocks()
  stubClipboard(undefined)
})

describe('PickupCodeCard', () => {
  it('PickupCodeCard shows code and meta', () => {
    const w = mountCard()
    expect(w.get('[role="status"]').text()).toBe(CODE)
    expect(w.text()).toContain('陳叔叔 · 10/03（六）')
    expect(w.text()).toContain('接送碼只會顯示這一次')
    expect(w.text()).toContain('遺失時可以重新產生，舊的接送碼會立即失效。')
  })

  it('PickupCodeCard renders one span per digit with extra gap after the third', () => {
    const digits = mountCard().findAll('.code__digit')
    expect(digits.map((d) => d.text())).toEqual(['0', '4', '8', '2', '1', '3'])
    expect(digits[2]?.classes()).toContain('is-group-end')
    expect(digits.filter((d) => d.classes().includes('is-group-end'))).toHaveLength(1)
  })

  it('PickupCodeCard copies code', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    stubClipboard(writeText)
    const w = mountCard()
    await copyButton(w).trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith(CODE)
    expect(copyButton(w).text()).toContain('已複製')
    expect(messages()[0]).toBe('已複製接送碼')
  })

  it('PickupCodeCard restores the copy label after 2 seconds and restarts on repeat clicks', async () => {
    vi.useFakeTimers()
    stubClipboard(() => Promise.resolve())
    const w = mountCard()
    await copyButton(w).trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1500)
    await copyButton(w).trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1500)
    expect(copyButton(w).text()).toContain('已複製')
    await vi.advanceTimersByTimeAsync(600)
    expect(copyButton(w).text()).toContain('複製接送碼')
  })

  it('PickupCodeCard copy failure message', async () => {
    stubClipboard(undefined)
    const w = mountCard()
    await copyButton(w).trigger('click')
    await flushPromises()
    expect(messages()[0]).toBe('無法自動複製，請手動記下')
    expect(useSnackbarStore().items[0]?.tone).toBe('info')
    expect(copyButton(w).text()).toContain('複製接送碼')
  })

  it('PickupCodeCard shows the manual hint when writeText rejects', async () => {
    stubClipboard(() => Promise.reject(new Error('NotAllowedError')))
    const w = mountCard()
    await copyButton(w).trigger('click')
    await flushPromises()
    expect(messages()[0]).toBe('無法自動複製，請手動記下')
    expect(copyButton(w).text()).toContain('複製接送碼')
  })

  it('PickupCodeCard done emits and stores nothing', async () => {
    stubClipboard(() => Promise.resolve())
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    const log = vi.spyOn(console, 'log')
    const w = mountCard()
    await copyButton(w).trigger('click')
    await flushPromises()
    const done = w.findAll('button').find((b) => b.text().includes('我已記下'))
    await done?.trigger('click')
    expect(w.emitted('done')).toHaveLength(1)
    expect(setItem).not.toHaveBeenCalled()
    expect(log).not.toHaveBeenCalled()
    for (const storage of [localStorage, sessionStorage]) {
      for (let i = 0; i < storage.length; i++) {
        expect(storage.getItem(storage.key(i) ?? '') ?? '').not.toContain(CODE)
      }
    }
  })

  it('PickupCodeCard clears its copied timer on unmount', async () => {
    vi.useFakeTimers()
    stubClipboard(() => Promise.resolve())
    const w = mountCard()
    await copyButton(w).trigger('click')
    await flushPromises()
    useSnackbarStore().clear()
    w.unmount()
    wrapper = null
    expect(vi.getTimerCount()).toBe(0)
  })
})
