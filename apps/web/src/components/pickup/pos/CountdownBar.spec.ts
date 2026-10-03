import type { VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { mountWithApp } from '@/test/helpers'
import CountdownBar from './CountdownBar.vue'

const NOW = new Date('2026-10-02T09:00:00Z').getTime()

let frames: Map<number, FrameRequestCallback>
let nextFrameId: number
let requestFrame: ReturnType<typeof vi.fn>
let cancelFrame: ReturnType<typeof vi.fn>

function stubReducedMotion(matches: boolean): void {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({ matches: query === '(prefers-reduced-motion: reduce)' && matches })),
  )
}

/** 依序執行目前排隊中的 rAF callback（一個 frame） */
async function runFrame(): Promise<void> {
  const pending = [...frames.entries()]
  frames.clear()
  for (const [, cb] of pending) cb(performance.now())
  await nextTick()
}

function fill(wrapper: VueWrapper): HTMLElement {
  return wrapper.find('[data-test=countdown-fill]').element as HTMLElement
}

async function mountBar(props: Record<string, unknown>, attrs: Record<string, string> = {}) {
  const { wrapper } = await mountWithApp(CountdownBar, { props: { ...props, ...attrs } })
  return wrapper
}

describe('CountdownBar', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
    vi.setSystemTime(NOW)
    frames = new Map()
    nextFrameId = 1
    requestFrame = vi.fn((cb: FrameRequestCallback) => {
      const id = nextFrameId++
      frames.set(id, cb)
      return id
    })
    cancelFrame = vi.fn((id: number) => {
      frames.delete(id)
    })
    vi.stubGlobal('requestAnimationFrame', requestFrame)
    vi.stubGlobal('cancelAnimationFrame', cancelFrame)
    stubReducedMotion(false)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('CountdownBar resumes from remaining ratio', async () => {
    const wrapper = await mountBar({ startedAt: NOW - 2000 })

    expect(fill(wrapper).style.transform).toBe('scaleX(0.6)')
    expect(fill(wrapper).style.transitionDuration).toBe('0ms')

    await runFrame()
    expect(fill(wrapper).style.transform).toBe('scaleX(0.6)')
    await runFrame()
    expect(fill(wrapper).style.transform).toBe('scaleX(0)')
    expect(fill(wrapper).style.transitionDuration).toBe('3000ms')
  })

  it('CountdownBar clamps future start', async () => {
    const wrapper = await mountBar({ startedAt: NOW + 10000 })

    expect(fill(wrapper).style.transform).toBe('scaleX(1)')
    expect(wrapper.attributes('aria-valuenow')).toBe('5000')
    await runFrame()
    await runFrame()
    expect(fill(wrapper).style.transitionDuration).toBe('5000ms')
  })

  it('CountdownBar reduced motion only changes color at expiry', async () => {
    stubReducedMotion(true)
    const wrapper = await mountBar({ startedAt: NOW })

    expect(fill(wrapper).style.transform).toBe('')
    expect(requestFrame).not.toHaveBeenCalled()
    vi.advanceTimersByTime(4999)
    await nextTick()
    expect(fill(wrapper).classList.contains('is-counting')).toBe(true)
    expect(fill(wrapper).classList.contains('is-done')).toBe(false)

    vi.advanceTimersByTime(1)
    await nextTick()
    expect(fill(wrapper).classList.contains('is-done')).toBe(true)
    expect(fill(wrapper).classList.contains('is-counting')).toBe(false)
    expect(fill(wrapper).style.transform).toBe('')
  })

  it('CountdownBar aria attributes and label passthrough', async () => {
    const wrapper = await mountBar({ startedAt: NOW - 1000 }, { 'aria-label': '王小明 送出倒數' })

    expect(wrapper.attributes('role')).toBe('progressbar')
    expect(wrapper.attributes('aria-valuemin')).toBe('0')
    expect(wrapper.attributes('aria-valuemax')).toBe('5000')
    expect(wrapper.attributes('aria-valuenow')).toBe('4000')
    expect(wrapper.attributes('aria-label')).toBe('王小明 送出倒數')

    const custom = await mountBar({ startedAt: NOW, durationMs: 3000 })
    expect(custom.attributes('aria-valuemax')).toBe('3000')
    expect(custom.attributes('aria-valuenow')).toBe('3000')
  })

  it('CountdownBar expired start renders empty bar without animation', async () => {
    const wrapper = await mountBar({ startedAt: NOW - 8000 })

    expect(fill(wrapper).style.transform).toBe('scaleX(0)')
    expect(fill(wrapper).style.transitionDuration).toBe('0ms')
    expect(wrapper.attributes('aria-valuenow')).toBe('0')
    expect(requestFrame).not.toHaveBeenCalled()
  })

  it('CountdownBar ignores prop changes and cancels frames on unmount', async () => {
    const wrapper = await mountBar({ startedAt: NOW - 2000 })

    await wrapper.setProps({ startedAt: NOW })
    expect(fill(wrapper).style.transform).toBe('scaleX(0.6)')
    expect(wrapper.attributes('aria-valuenow')).toBe('3000')

    const scheduled = requestFrame.mock.results.map((r) => r.value as number)
    expect(scheduled.length).toBeGreaterThan(0)
    wrapper.unmount()
    expect(cancelFrame).toHaveBeenCalledWith(scheduled[0])
    expect(frames.size).toBe(0)
  })
})
