import { afterEach, describe, expect, it } from 'vitest'
import { isTopOverlay, pushOverlay, removeOverlay } from './overlayStack'

const a = Symbol('a')
const b = Symbol('b')

afterEach(() => {
  removeOverlay(a)
  removeOverlay(b)
  document.body.style.overflow = ''
})

describe('overlayStack', () => {
  it('overlayStack tracks the topmost overlay', () => {
    pushOverlay(a)
    pushOverlay(b)
    expect(isTopOverlay(b)).toBe(true)
    expect(isTopOverlay(a)).toBe(false)
    removeOverlay(b)
    expect(isTopOverlay(a)).toBe(true)
  })

  it('overlayStack restores the original overflow only after the last overlay is removed', () => {
    document.body.style.overflow = 'auto'
    pushOverlay(a)
    pushOverlay(b)
    expect(document.body.style.overflow).toBe('hidden')
    removeOverlay(a)
    expect(document.body.style.overflow).toBe('hidden')
    removeOverlay(b)
    expect(document.body.style.overflow).toBe('auto')
  })

  it('overlayStack ignores duplicate push and unknown remove', () => {
    document.body.style.overflow = 'auto'
    pushOverlay(a)
    pushOverlay(a)
    removeOverlay(b)
    expect(document.body.style.overflow).toBe('hidden')
    removeOverlay(a)
    expect(document.body.style.overflow).toBe('auto')
  })
})
