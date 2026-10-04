import { DOMWrapper, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import ParentBottomSheet from './ParentBottomSheet.vue'
import source from './ParentBottomSheet.vue?raw'

let mounted: VueWrapper[] = []

function mountSheet(
  props: Record<string, unknown> = {},
  slots: Record<string, string> = { default: '<input aria-label="姓名"><button type="button">確定</button>' },
): VueWrapper {
  const wrapper = mount(ParentBottomSheet, {
    attachTo: document.body,
    props: { modelValue: true, title: '新增接送人', ...props },
    slots,
  })
  mounted.push(wrapper)
  return wrapper
}

function dialog(): HTMLElement | null {
  return document.body.querySelector('[role="dialog"]')
}

function q(selector: string): DOMWrapper<Element> {
  const el = document.body.querySelector(selector)
  if (!el) throw new Error(`找不到 ${selector}`)
  return new DOMWrapper(el)
}

function pointer(target: EventTarget, type: string, clientY: number): void {
  target.dispatchEvent(new MouseEvent(type, { clientY, bubbles: true, button: 0 }))
}

function escape(): void {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
}

afterEach(() => {
  for (const w of mounted) w.unmount()
  mounted = []
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

describe('ParentBottomSheet', () => {
  it('ParentBottomSheet renders dialog when open', async () => {
    const wrapper = mountSheet()
    const el = dialog()
    expect(el).not.toBeNull()
    expect(el?.getAttribute('aria-modal')).toBe('true')
    const labelId = el?.getAttribute('aria-labelledby') ?? ''
    expect(labelId).not.toBe('')
    expect(document.getElementById(labelId)?.textContent?.trim()).toBe('新增接送人')
    expect(el?.textContent).toContain('新增接送人')

    await wrapper.setProps({ modelValue: false })
    expect(dialog()).toBeNull()
  })

  it('ParentBottomSheet renders nothing when modelValue is false', () => {
    mountSheet({ modelValue: false })
    expect(dialog()).toBeNull()
    expect(document.body.querySelector('.sheet-scrim')).toBeNull()
  })

  it('ParentBottomSheet closes on scrim and escape', async () => {
    const wrapper = mountSheet()
    await q('.sheet-scrim').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([false])
    escape()
    expect(wrapper.emitted('update:modelValue')?.[1]).toEqual([false])
  })

  it('ParentBottomSheet non dismissible ignores close', async () => {
    const wrapper = mountSheet({ dismissible: false })
    await q('.sheet-scrim').trigger('click')
    escape()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('ParentBottomSheet restores focus', async () => {
    const outside = document.createElement('button')
    outside.textContent = '開啟'
    document.body.appendChild(outside)
    outside.focus()
    expect(document.activeElement).toBe(outside)

    const wrapper = mountSheet({ modelValue: false })
    await wrapper.setProps({ modelValue: true })
    await nextTick()
    expect(dialog()?.contains(document.activeElement)).toBe(true)

    await wrapper.setProps({ modelValue: false })
    await nextTick()
    expect(document.activeElement).toBe(outside)
  })

  it('ParentBottomSheet focuses the sheet itself when it has no focusable content', async () => {
    const wrapper = mountSheet({ modelValue: false, dismissible: false }, { default: '<p>只有文字</p>' })
    await wrapper.setProps({ modelValue: true })
    await nextTick()
    expect(document.activeElement).toBe(dialog())
  })

  it('ParentBottomSheet close button follows dismissible', async () => {
    const wrapper = mountSheet()
    const close = dialog()?.querySelector('button[aria-label="關閉"]')
    expect(close).not.toBeNull()
    await new DOMWrapper(close as Element).trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([false])

    await wrapper.setProps({ dismissible: false })
    expect(dialog()?.querySelector('button[aria-label="關閉"]')).toBeNull()
  })

  it('ParentBottomSheet drags only from handle and header', async () => {
    const wrapper = mountSheet()
    // 內容區：不攔截拖曳
    const body = q('.sheet__body').element
    pointer(body, 'pointerdown', 300)
    pointer(window, 'pointermove', 420)
    pointer(window, 'pointerup', 420)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()

    // 標題列下移超過 80px：關閉
    pointer(q('.sheet__header').element, 'pointerdown', 300)
    pointer(window, 'pointermove', 420)
    pointer(window, 'pointerup', 420)
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([false])
  })

  it('ParentBottomSheet drag under threshold springs back without emitting', async () => {
    const wrapper = mountSheet()
    const sheet = q('.sheet').element as HTMLElement
    pointer(q('.sheet__header').element, 'pointerdown', 300)
    pointer(window, 'pointermove', 360)
    await nextTick()
    expect(sheet.style.transform).toBe('translateY(60px)')
    pointer(window, 'pointerup', 360)
    await nextTick()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(sheet.style.transform).toBe('')
  })

  it('ParentBottomSheet drag from handle follows finger 1:1 and ignores upward drag', async () => {
    mountSheet()
    const sheet = q('.sheet').element as HTMLElement
    pointer(q('.sheet__handle').element, 'pointerdown', 300)
    pointer(window, 'pointermove', 250)
    await nextTick()
    expect(sheet.style.transform).toBe('')
    pointer(window, 'pointermove', 345)
    await nextTick()
    expect(sheet.style.transform).toBe('translateY(45px)')
    pointer(window, 'pointerup', 345)
  })

  it('ParentBottomSheet non dismissible drag only dampens then springs back', async () => {
    const wrapper = mountSheet({ dismissible: false })
    const sheet = q('.sheet').element as HTMLElement
    pointer(q('.sheet__header').element, 'pointerdown', 300)
    pointer(window, 'pointermove', 340)
    await nextTick()
    expect(sheet.style.transform).toBe('translateY(10px)')
    pointer(window, 'pointermove', 700)
    await nextTick()
    expect(sheet.style.transform).toBe('translateY(24px)')
    pointer(window, 'pointerup', 700)
    await nextTick()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(sheet.style.transform).toBe('')
  })

  it('ParentBottomSheet pointerdown on close button does not start a drag', async () => {
    const wrapper = mountSheet()
    const close = dialog()?.querySelector('button[aria-label="關閉"]') as Element
    pointer(close, 'pointerdown', 300)
    pointer(window, 'pointermove', 500)
    pointer(window, 'pointerup', 500)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('ParentBottomSheet traps Tab inside the sheet', async () => {
    mountSheet()
    const buttons = Array.from(dialog()?.querySelectorAll<HTMLElement>('button, input') ?? [])
    const first = buttons[0] as HTMLElement
    const last = buttons[buttons.length - 1] as HTMLElement
    last.focus()
    await new DOMWrapper(dialog() as Element).trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(first)
    await new DOMWrapper(dialog() as Element).trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
  })

  it('ParentBottomSheet locks body scroll while open and restores it on close and unmount', async () => {
    document.body.style.overflow = 'auto'
    const wrapper = mountSheet({ modelValue: false })
    expect(document.body.style.overflow).toBe('auto')
    await wrapper.setProps({ modelValue: true })
    expect(document.body.style.overflow).toBe('hidden')
    await wrapper.setProps({ modelValue: false })
    expect(document.body.style.overflow).toBe('auto')

    await wrapper.setProps({ modelValue: true })
    expect(document.body.style.overflow).toBe('hidden')
    wrapper.unmount()
    mounted = mounted.filter((w) => w !== wrapper)
    expect(document.body.style.overflow).toBe('auto')
  })

  it('ParentBottomSheet keeps body locked when two sheets swap in the same tick', async () => {
    document.body.style.overflow = 'auto'
    const a = mountSheet({ title: 'A' })
    const b = mountSheet({ title: 'B', modelValue: false })
    expect(document.body.style.overflow).toBe('hidden')
    await Promise.all([a.setProps({ modelValue: false }), b.setProps({ modelValue: true })])
    await nextTick()
    expect(document.body.style.overflow).toBe('hidden')
    await b.setProps({ modelValue: false })
    expect(document.body.style.overflow).toBe('auto')
  })

  it('ParentBottomSheet restores body scroll after overlapping sheets close in any order', async () => {
    document.body.style.overflow = 'auto'
    const a = mountSheet({ title: 'A' })
    const b = mountSheet({ title: 'B' })
    await a.setProps({ modelValue: false })
    expect(document.body.style.overflow).toBe('hidden')
    await b.setProps({ modelValue: false })
    expect(document.body.style.overflow).toBe('auto')
  })

  it('ParentBottomSheet pulls focus back into the sheet when it lands outside', async () => {
    const outside = document.createElement('button')
    outside.textContent = '背景'
    document.body.appendChild(outside)
    mountSheet({ dismissible: false })
    await nextTick()
    outside.focus()
    await nextTick()
    expect(dialog()?.contains(document.activeElement)).toBe(true)
  })

  it('ParentBottomSheet Tab from outside the sheet enters the sheet', async () => {
    mountSheet({ dismissible: false })
    await nextTick()
    ;(document.activeElement as HTMLElement | null)?.blur()
    expect(document.activeElement).toBe(document.body)
    const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })
    document.body.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    expect(dialog()?.contains(document.activeElement)).toBe(true)
  })

  it('ParentBottomSheet Escape closes only the topmost of stacked sheets', async () => {
    const a = mountSheet({ title: 'A' })
    const b = mountSheet({ title: 'B' })
    escape()
    expect(b.emitted('update:modelValue')?.[0]).toEqual([false])
    expect(a.emitted('update:modelValue')).toBeUndefined()
    await b.setProps({ modelValue: false })
    escape()
    expect(a.emitted('update:modelValue')?.[0]).toEqual([false])
  })

  it('ParentBottomSheet does not restore focus to an element that was removed', async () => {
    const trigger = document.createElement('button')
    document.body.appendChild(trigger)
    trigger.focus()
    const wrapper = mountSheet({ modelValue: false })
    await wrapper.setProps({ modelValue: true })
    await nextTick()
    trigger.remove()
    await wrapper.setProps({ modelValue: false })
    expect(document.activeElement).not.toBe(trigger)
  })

  it('ParentBottomSheet renders footer slot below the body only when provided', () => {
    mountSheet({}, { default: '<p>內容</p>', footer: '<button type="button">儲存</button>' })
    const footer = document.body.querySelector('.sheet__footer')
    expect(footer?.textContent).toContain('儲存')
    expect(document.body.querySelector('.sheet__body + .sheet__footer')).not.toBeNull()
    for (const w of mounted) w.unmount()
    mounted = []
    document.body.innerHTML = ''
    mountSheet({}, { default: '<p>內容</p>' })
    expect(document.body.querySelector('.sheet__footer')).toBeNull()
  })

  it('ParentBottomSheet fullHeight adds is-full class', async () => {
    const wrapper = mountSheet()
    expect(q('.sheet').classes()).not.toContain('is-full')
    await wrapper.setProps({ fullHeight: true })
    expect(q('.sheet').classes()).toContain('is-full')
  })

  it('ParentBottomSheet teleports to body and removes document listeners on unmount', async () => {
    const wrapper = mountSheet()
    expect(document.body.contains(dialog())).toBe(true)
    expect(wrapper.element.contains(dialog())).toBe(false)
    wrapper.unmount()
    mounted = mounted.filter((w) => w !== wrapper)
    expect(dialog()).toBeNull()
    escape()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('ParentBottomSheet style follows design decisions', () => {
    expect(source).toMatch(/position:\s*fixed/)
    expect(source).toMatch(/100dvh/)
    expect(source).toMatch(/\.sheet\s*\{[^}]*max-height:\s*calc\(100% - 56px\)/)
    expect(source).toMatch(/\.sheet\.is-full\s*\{[^}]*height:\s*calc\(100% - 56px\)/)
    expect(source).toMatch(/\.sheet__body\s*\{[^}]*overflow-y:\s*auto/)
    expect(source).toMatch(/\.sheet__footer\s*\{[^}]*env\(safe-area-inset-bottom\)/)
    expect(source).toMatch(/\.sheet__drag\s*\{[^}]*touch-action:\s*none/)
    expect(source).toMatch(/var\(--m3-surface-container-low\)/)
    expect(source).toMatch(/var\(--m3-shape-extra-large\)/)
    expect(source).toMatch(/opacity:\s*0\.32/)
    expect(source).toMatch(/prefers-reduced-motion/)
    // snackbar（z-index 20）要蓋在 sheet 之上
    const z = Number(/\.sheet-layer\s*\{[^}]*z-index:\s*(\d+)/.exec(source)?.[1])
    expect(z).toBeLessThan(20)
    expect(z).toBeGreaterThan(5)
  })
})
