import { describe, expect, it, vi } from 'vitest'
import { useSwipeReveal } from './useSwipeReveal'

function pointer(type: string, clientX: number, clientY = 100, pointerId = 1): PointerEvent {
  return new PointerEvent(type, { clientX, clientY, pointerId })
}

type Swipe = ReturnType<typeof useSwipeReveal>

function drag(s: Swipe, fromX: number, toX: number, opts: { y?: number; toY?: number; id?: number } = {}) {
  const y = opts.y ?? 100
  const id = opts.id ?? 1
  s.onPointerDown(pointer('pointerdown', fromX, y, id))
  s.onPointerMove(pointer('pointermove', toX, opts.toY ?? y, id))
  s.onPointerUp(pointer('pointerup', toX, opts.toY ?? y, id))
}

function stubReducedMotion(matches: boolean): void {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({ matches: query === '(prefers-reduced-motion: reduce)' && matches })),
  )
}

describe('useSwipeReveal', () => {
  it('useSwipeReveal opens past threshold', () => {
    const s = useSwipeReveal({ revealWidth: 84 })

    drag(s, 300, 250)

    expect(s.isOpen.value).toBe(true)
    expect(s.dragX.value).toBe(-84)
    expect(s.dragging.value).toBe(false)
  })

  it('useSwipeReveal rebounds below threshold', () => {
    const s = useSwipeReveal({ revealWidth: 84 })

    drag(s, 300, 270)
    expect(s.isOpen.value).toBe(false)
    expect(s.dragX.value).toBe(0)

    s.onPointerDown(pointer('pointerdown', 300))
    s.onPointerMove(pointer('pointermove', 280))
    expect(s.dragging.value).toBe(true)
    expect(s.dragX.value).toBe(-20)
    s.onPointerMove(pointer('pointermove', 100))
    expect(s.dragX.value).toBe(-84)
    s.onPointerMove(pointer('pointermove', 400))
    expect(s.dragX.value).toBe(0)
  })

  it('useSwipeReveal closes by dragging right when open', () => {
    const s = useSwipeReveal({ revealWidth: 84 })
    drag(s, 300, 250)
    expect(s.isOpen.value).toBe(true)

    s.onPointerDown(pointer('pointerdown', 200))
    s.onPointerMove(pointer('pointermove', 260))
    expect(s.dragX.value).toBe(-24)
    s.onPointerUp(pointer('pointerup', 260))

    expect(s.isOpen.value).toBe(false)
    expect(s.dragX.value).toBe(0)
  })

  it('useSwipeReveal ignores vertical scroll and disabled state', () => {
    const s = useSwipeReveal({ revealWidth: 84 })
    s.onPointerDown(pointer('pointerdown', 300, 100))
    s.onPointerMove(pointer('pointermove', 295, 140))

    expect(s.dragging.value).toBe(false)
    expect(s.dragX.value).toBe(0)
    // 放棄後的後續 move / up 不再作用
    s.onPointerMove(pointer('pointermove', 200, 140))
    s.onPointerUp(pointer('pointerup', 200, 140))
    expect(s.dragX.value).toBe(0)
    expect(s.isOpen.value).toBe(false)

    let disabled = true
    const d = useSwipeReveal({ revealWidth: 84, disabled: () => disabled })
    drag(d, 300, 200)
    expect(d.isOpen.value).toBe(false)
    expect(d.dragX.value).toBe(0)
    expect(d.dragging.value).toBe(false)

    disabled = false
    drag(d, 300, 200)
    expect(d.isOpen.value).toBe(true)
  })

  it('useSwipeReveal uses getter width', () => {
    let w = 84
    const s = useSwipeReveal({ revealWidth: () => w })

    w = 168
    drag(s, 300, 100)

    expect(s.isOpen.value).toBe(true)
    expect(s.dragX.value).toBe(-168)
  })

  it('useSwipeReveal ignores a second pointer while dragging', () => {
    const s = useSwipeReveal({ revealWidth: 84 })
    s.onPointerDown(pointer('pointerdown', 300, 100, 1))
    s.onPointerMove(pointer('pointermove', 290, 100, 1))

    s.onPointerDown(pointer('pointerdown', 50, 100, 2))
    s.onPointerMove(pointer('pointermove', 0, 100, 2))
    s.onPointerUp(pointer('pointerup', 0, 100, 2))
    expect(s.dragging.value).toBe(true)
    expect(s.dragX.value).toBe(-10)

    s.onPointerMove(pointer('pointermove', 240, 100, 1))
    s.onPointerUp(pointer('pointerup', 240, 100, 1))
    expect(s.isOpen.value).toBe(true)
    expect(s.dragX.value).toBe(-84)
  })

  it('useSwipeReveal pointercancel restores resting position without toggling', () => {
    const s = useSwipeReveal({ revealWidth: 84 })
    s.onPointerDown(pointer('pointerdown', 300))
    s.onPointerMove(pointer('pointermove', 220))
    s.onPointerCancel(pointer('pointercancel', 220))

    expect(s.dragging.value).toBe(false)
    expect(s.isOpen.value).toBe(false)
    expect(s.dragX.value).toBe(0)

    drag(s, 300, 250)
    s.onPointerDown(pointer('pointerdown', 200))
    s.onPointerMove(pointer('pointermove', 280))
    s.onPointerCancel(pointer('pointercancel', 280))
    expect(s.isOpen.value).toBe(true)
    expect(s.dragX.value).toBe(-84)
  })

  it('useSwipeReveal close resets open state', () => {
    const s = useSwipeReveal({ revealWidth: 84 })
    drag(s, 300, 250)

    s.close()

    expect(s.isOpen.value).toBe(false)
    expect(s.dragX.value).toBe(0)
  })

  it('useSwipeReveal sets reboundInstant from reduced motion preference', () => {
    stubReducedMotion(true)
    const s = useSwipeReveal()
    s.onPointerDown(pointer('pointerdown', 300))
    expect(s.reboundInstant.value).toBe(true)
    s.onPointerUp(pointer('pointerup', 300))

    stubReducedMotion(false)
    s.onPointerDown(pointer('pointerdown', 300))
    expect(s.reboundInstant.value).toBe(false)
  })
})
