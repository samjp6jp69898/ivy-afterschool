import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PullToRefresh from './PullToRefresh.vue'

type Refresh = () => Promise<unknown>

function deferred(): { promise: Promise<void>; resolve: () => void; reject: (e: Error) => void } {
  let resolve!: () => void
  let reject!: (e: Error) => void
  const promise = new Promise<void>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

interface Point {
  x?: number
  y: number
  id?: number
}

/** 以目前仍按在螢幕上的觸點（touches）派發 TouchEvent */
function touchPoints(wrapper: VueWrapper, type: string, points: Point[]): TouchEvent {
  const target = wrapper.element
  const touches = points.map((p, i) => new Touch({ identifier: p.id ?? i, target, clientX: p.x ?? 0, clientY: p.y }))
  const event = new TouchEvent(type, { touches, cancelable: true, bubbles: true })
  target.dispatchEvent(event)
  return event
}

function touch(wrapper: VueWrapper, type: string, clientY?: number): TouchEvent {
  return touchPoints(wrapper, type, clientY === undefined ? [] : [{ y: clientY }])
}

async function pull(wrapper: VueWrapper, toY: number): Promise<void> {
  touch(wrapper, 'touchstart', 0)
  touch(wrapper, 'touchmove', toY)
  touch(wrapper, 'touchend')
  await flushPromises()
}

function mountPtr(onRefresh: Refresh, props: Record<string, unknown> = {}): VueWrapper {
  return mount(PullToRefresh, {
    props: { onRefresh, ...props },
    slots: { default: '<ul><li>王小明已到班</li></ul>' },
  })
}

function indicator(wrapper: VueWrapper) {
  return wrapper.get('[role="status"]')
}

function indicatorHeight(wrapper: VueWrapper): string | undefined {
  return indicator(wrapper).attributes('style')?.match(/height: ([^;]+)/)?.[1]
}

describe('PullToRefresh', () => {
  afterEach(() => {
    Reflect.deleteProperty(window, 'scrollY')
  })

  it('PullToRefresh triggers past threshold', async () => {
    const pending = deferred()
    const onRefresh = vi.fn<Refresh>(() => pending.promise)
    const wrapper = mountPtr(onRefresh)
    expect(wrapper.text()).toContain('王小明已到班')

    await pull(wrapper, 200)
    expect(onRefresh).toHaveBeenCalledTimes(1)
    expect(indicator(wrapper).attributes('aria-label')).toBe('重新整理中')
    // 重新整理中停在 56px
    expect(indicatorHeight(wrapper)).toBe('56px')
    expect(wrapper.get('.ptr__content').attributes('style')).toContain('translateY(56px)')
    expect(wrapper.get('.ptr__text').text()).toBe('重新整理中…')

    pending.resolve()
    await flushPromises()
    expect(indicator(wrapper).attributes('aria-label')).toBeUndefined()
    expect(indicatorHeight(wrapper)).toBe('0px')
    expect(wrapper.get('.ptr__content').attributes('style')).toBeUndefined()
  })

  it('PullToRefresh ignores short pull', async () => {
    const onRefresh = vi.fn<Refresh>(() => Promise.resolve())
    const wrapper = mountPtr(onRefresh)
    touch(wrapper, 'touchstart', 0)
    touch(wrapper, 'touchmove', 100)
    await flushPromises()
    // 位移 = 100 × 0.5 = 50，未達門檻 64
    expect(indicatorHeight(wrapper)).toBe('50px')
    expect(wrapper.get('.ptr__text').text()).toBe('下拉重新整理')
    touch(wrapper, 'touchend')
    await flushPromises()
    expect(onRefresh).not.toHaveBeenCalled()
    expect(indicatorHeight(wrapper)).toBe('0px')
  })

  it('PullToRefresh no duplicate while refreshing', async () => {
    const pending = deferred()
    const onRefresh = vi.fn<Refresh>(() => pending.promise)
    const wrapper = mountPtr(onRefresh)
    await pull(wrapper, 200)
    expect(onRefresh).toHaveBeenCalledTimes(1)

    touch(wrapper, 'touchstart', 0)
    const move = touch(wrapper, 'touchmove', 300)
    touch(wrapper, 'touchend')
    await flushPromises()
    expect(onRefresh).toHaveBeenCalledTimes(1)
    // 重新整理中不再位移、也不攔截手勢
    expect(move.defaultPrevented).toBe(false)
    expect(indicatorHeight(wrapper)).toBe('56px')

    pending.resolve()
    await flushPromises()
    await pull(wrapper, 200)
    expect(onRefresh).toHaveBeenCalledTimes(2)
  })

  it('PullToRefresh disabled', async () => {
    const onRefresh = vi.fn<Refresh>(() => Promise.resolve())
    const wrapper = mountPtr(onRefresh, { disabled: true })
    touch(wrapper, 'touchstart', 0)
    const move = touch(wrapper, 'touchmove', 400)
    touch(wrapper, 'touchend')
    await flushPromises()
    expect(onRefresh).not.toHaveBeenCalled()
    expect(move.defaultPrevented).toBe(false)
    expect(indicatorHeight(wrapper)).toBe('0px')
  })

  it('PullToRefresh collapses after reject', async () => {
    const pending = deferred()
    const onRefresh = vi.fn<Refresh>(() => pending.promise)
    const wrapper = mountPtr(onRefresh)
    await pull(wrapper, 200)
    expect(indicator(wrapper).attributes('aria-label')).toBe('重新整理中')

    pending.reject(new Error('network'))
    await flushPromises()
    expect(indicator(wrapper).attributes('aria-label')).toBeUndefined()
    expect(indicatorHeight(wrapper)).toBe('0px')
  })

  it('PullToRefresh caps pull at twice threshold and arms past threshold', async () => {
    const onRefresh = vi.fn<Refresh>(() => Promise.resolve())
    const wrapper = mountPtr(onRefresh)
    touch(wrapper, 'touchstart', 0)
    touch(wrapper, 'touchmove', 1000)
    await flushPromises()
    expect(indicatorHeight(wrapper)).toBe('128px')
    expect(wrapper.get('.ptr__content').attributes('style')).toContain('translateY(128px)')
    expect(wrapper.get('.ptr__text').text()).toBe('放開即可重新整理')
    // 可見文字不重複朗讀，狀態由 role=status 的 aria-label 表達
    expect(wrapper.get('.ptr__text').attributes('aria-hidden')).toBe('true')

    const small = mountPtr(onRefresh, { threshold: 100 })
    await pull(small, 180)
    expect(onRefresh).not.toHaveBeenCalled()
  })

  it('PullToRefresh prevents default only while pulling down', () => {
    const wrapper = mountPtr(() => Promise.resolve())
    touch(wrapper, 'touchstart', 300)
    const up = touch(wrapper, 'touchmove', 200)
    expect(up.defaultPrevented).toBe(false)
    const down = touch(wrapper, 'touchmove', 400)
    expect(down.defaultPrevented).toBe(true)
  })

  it('PullToRefresh only starts at page top', async () => {
    Object.defineProperty(window, 'scrollY', { configurable: true, get: () => 120 })
    const onRefresh = vi.fn<Refresh>(() => Promise.resolve())
    const wrapper = mountPtr(onRefresh)
    touch(wrapper, 'touchstart', 0)
    const move = touch(wrapper, 'touchmove', 300)
    touch(wrapper, 'touchend')
    await flushPromises()
    expect(move.defaultPrevented).toBe(false)
    expect(onRefresh).not.toHaveBeenCalled()
    expect(indicatorHeight(wrapper)).toBe('0px')
  })

  it('PullToRefresh keeps indicator on touchcancel while refreshing', async () => {
    const pending = deferred()
    const wrapper = mountPtr(() => pending.promise)
    await pull(wrapper, 200)
    touch(wrapper, 'touchstart', 0)
    touch(wrapper, 'touchcancel')
    await flushPromises()
    expect(indicatorHeight(wrapper)).toBe('56px')
    expect(indicator(wrapper).attributes('aria-label')).toBe('重新整理中')
    expect(wrapper.get('.ptr__content').attributes('style')).toContain('translateY(56px)')

    pending.resolve()
    await flushPromises()
    expect(indicatorHeight(wrapper)).toBe('0px')
  })

  it('PullToRefresh touchcancel while pulling collapses without refresh', async () => {
    const onRefresh = vi.fn<Refresh>(() => Promise.resolve())
    const wrapper = mountPtr(onRefresh)
    touch(wrapper, 'touchstart', 0)
    touch(wrapper, 'touchmove', 200)
    touch(wrapper, 'touchcancel')
    touch(wrapper, 'touchend')
    await flushPromises()
    expect(onRefresh).not.toHaveBeenCalled()
    expect(indicatorHeight(wrapper)).toBe('0px')
  })

  it('PullToRefresh ignores near horizontal swipe', async () => {
    const onRefresh = vi.fn<Refresh>(() => Promise.resolve())
    const wrapper = mountPtr(onRefresh)
    touchPoints(wrapper, 'touchstart', [{ x: 200, y: 100 }])
    const swipe = touchPoints(wrapper, 'touchmove', [{ x: 80, y: 104 }])
    expect(swipe.defaultPrevented).toBe(false)
    // 判定為水平後，同一個手勢再往下也不接管（不中途搶走 chip 列等水平捲動）
    const later = touchPoints(wrapper, 'touchmove', [{ x: 60, y: 300 }])
    expect(later.defaultPrevented).toBe(false)
    touch(wrapper, 'touchend')
    await flushPromises()
    expect(onRefresh).not.toHaveBeenCalled()
    expect(indicatorHeight(wrapper)).toBe('0px')

    // 起始移動在判定距離內不攔截；明確往下（|dy| > |dx|）才接管
    touchPoints(wrapper, 'touchstart', [{ x: 100, y: 0 }])
    const tiny = touchPoints(wrapper, 'touchmove', [{ x: 102, y: 4 }])
    expect(tiny.defaultPrevented).toBe(false)
    const down = touchPoints(wrapper, 'touchmove', [{ x: 110, y: 60 }])
    expect(down.defaultPrevented).toBe(true)
    await flushPromises()
    expect(indicatorHeight(wrapper)).toBe('30px')
  })

  it('PullToRefresh multi touch does not trigger early', async () => {
    const onRefresh = vi.fn<Refresh>(() => Promise.resolve())
    const wrapper = mountPtr(onRefresh)
    touchPoints(wrapper, 'touchstart', [{ y: 0, id: 0 }])
    touchPoints(wrapper, 'touchmove', [{ y: 200, id: 0 }])
    // 第二指按下再抬起，第一指仍按著
    touchPoints(wrapper, 'touchstart', [{ y: 200, id: 0 }, { x: 50, y: 300, id: 1 }])
    touchPoints(wrapper, 'touchend', [{ y: 200, id: 0 }])
    await flushPromises()
    expect(onRefresh).not.toHaveBeenCalled()
    // 多指視為取消，最後一指抬起也不觸發
    touchPoints(wrapper, 'touchend', [])
    await flushPromises()
    expect(onRefresh).not.toHaveBeenCalled()
    expect(indicatorHeight(wrapper)).toBe('0px')
  })

  it('PullToRefresh cancels when page scrolls during pull', async () => {
    const onRefresh = vi.fn<Refresh>(() => Promise.resolve())
    const wrapper = mountPtr(onRefresh)
    touch(wrapper, 'touchstart', 0)
    touch(wrapper, 'touchmove', 100)
    await flushPromises()
    expect(indicatorHeight(wrapper)).toBe('50px')
    Object.defineProperty(window, 'scrollY', { configurable: true, get: () => 40 })
    const move = touch(wrapper, 'touchmove', 200)
    await flushPromises()
    expect(move.defaultPrevented).toBe(false)
    expect(indicatorHeight(wrapper)).toBe('0px')
    touch(wrapper, 'touchend')
    await flushPromises()
    expect(onRefresh).not.toHaveBeenCalled()
  })

  it('PullToRefresh triggers exactly at threshold and arms arrow', async () => {
    const onRefresh = vi.fn<Refresh>(() => Promise.resolve())
    const wrapper = mountPtr(onRefresh)
    touch(wrapper, 'touchstart', 0)
    touch(wrapper, 'touchmove', 126)
    await flushPromises()
    // 63px：未達門檻，箭頭未轉
    expect(wrapper.get('.ptr__arrow').classes()).not.toContain('is-armed')
    touch(wrapper, 'touchmove', 128)
    await flushPromises()
    // 64px：恰好等於門檻即視為達標
    expect(indicatorHeight(wrapper)).toBe('64px')
    expect(wrapper.get('.ptr__arrow').classes()).toContain('is-armed')
    expect(wrapper.get('.ptr__text').text()).toBe('放開即可重新整理')
    touch(wrapper, 'touchend')
    await flushPromises()
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })
})
