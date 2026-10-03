import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSnackbarStore } from './snackbar'

describe('snackbarStore', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setActivePinia(createPinia())
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('snackbarStore auto dismisses after duration', () => {
    const store = useSnackbarStore()
    store.show('已送出')
    expect(store.items[0]?.message).toBe('已送出')
    expect(store.items[0]?.tone).toBe('info')
    expect(store.items[0]?.duration).toBe(4000)

    vi.advanceTimersByTime(3999)
    expect(store.items).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(store.items).toHaveLength(0)
  })

  it('snackbarStore does not time queued item before it shows', () => {
    const store = useSnackbarStore()
    store.show('A')
    vi.advanceTimersByTime(1000)
    store.show('B')
    // B 排隊期間不計時：A 到期（再 3000ms）後 B 才開始算 4000ms
    vi.advanceTimersByTime(3000)
    expect(store.items.map((i) => i.message)).toEqual(['B'])
    vi.advanceTimersByTime(3999)
    expect(store.items.map((i) => i.message)).toEqual(['B'])
    vi.advanceTimersByTime(1)
    expect(store.items).toHaveLength(0)
  })

  it('snackbarStore queues and times sequentially', () => {
    const store = useSnackbarStore()
    store.show('A')
    store.show('B')
    vi.advanceTimersByTime(4000)
    expect(store.items[0]?.message).toBe('B')
    vi.advanceTimersByTime(4000)
    expect(store.items).toHaveLength(0)
  })

  it('snackbarStore caps queue at three', () => {
    const store = useSnackbarStore()
    store.show('A')
    store.show('B')
    store.show('C')
    store.show('D')
    expect(store.items.map((i) => i.message)).toEqual(['A', 'C', 'D'])
    // 正在顯示的 A 計時不受影響
    vi.advanceTimersByTime(4000)
    expect(store.items.map((i) => i.message)).toEqual(['C', 'D'])
  })

  it('snackbarStore sticky when duration zero', () => {
    const store = useSnackbarStore()
    const id = store.show('網路中斷', { duration: 0 })
    vi.advanceTimersByTime(60000)
    expect(store.items.map((i) => i.message)).toEqual(['網路中斷'])
    store.dismiss(id)
    expect(store.items).toHaveLength(0)
  })

  it('snackbarStore dismissing head starts next timer', () => {
    const store = useSnackbarStore()
    const first = store.show('A', { duration: 0 })
    store.show('B', { duration: 2000, tone: 'error' })
    vi.advanceTimersByTime(10000)
    store.dismiss(first)
    expect(store.items[0]).toMatchObject({ message: 'B', tone: 'error', duration: 2000 })
    vi.advanceTimersByTime(1999)
    expect(store.items).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(store.items).toHaveLength(0)
  })

  it('snackbarStore dismissing queued item keeps head timer', () => {
    const store = useSnackbarStore()
    store.show('A')
    const queued = store.show('B')
    vi.advanceTimersByTime(3000)
    store.dismiss(queued)
    expect(store.items.map((i) => i.message)).toEqual(['A'])
    vi.advanceTimersByTime(1000)
    expect(store.items).toHaveLength(0)
  })

  it('snackbarStore returns distinct ids and keeps action', () => {
    const store = useSnackbarStore()
    const onClick = vi.fn()
    const a = store.show('已刪除', { action: { label: '復原', onClick } })
    const b = store.show('B')
    expect(a).not.toBe(b)
    expect(store.items.map((i) => i.id)).toEqual([a, b])
    expect(store.items[0]?.action).toEqual({ label: '復原', onClick })
    expect(store.items[1]?.action).toBeUndefined()
  })

  it('snackbarStore clear empties queue and cancels timer', () => {
    const store = useSnackbarStore()
    store.show('A')
    store.show('B')
    store.clear()
    expect(store.items).toHaveLength(0)
    // 清空後新加入的項目重新計時，不會被舊計時器提早移除
    vi.advanceTimersByTime(2000)
    store.show('C')
    vi.advanceTimersByTime(2000)
    expect(store.items.map((i) => i.message)).toEqual(['C'])
    vi.advanceTimersByTime(2000)
    expect(store.items).toHaveLength(0)
  })
})
