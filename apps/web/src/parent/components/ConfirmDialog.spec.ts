import { DOMWrapper, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import ConfirmDialog from './ConfirmDialog.vue'
import ParentBottomSheet from './ParentBottomSheet.vue'
import source from './ConfirmDialog.vue?raw'

let mounted: VueWrapper[] = []

function mountDialog(props: Record<string, unknown> = {}, slots: Record<string, string> = {}): VueWrapper {
  const wrapper = mount(ConfirmDialog, {
    attachTo: document.body,
    props: { open: true, title: '取消接送？', confirmLabel: '確定取消', ...props },
    slots,
  })
  mounted.push(wrapper)
  return wrapper
}

function alertdialog(): HTMLElement | null {
  return document.body.querySelector('[role="alertdialog"]')
}

function button(label: string): DOMWrapper<Element> {
  const el = Array.from(document.body.querySelectorAll('[role="alertdialog"] button')).find(
    (b) => b.textContent?.trim() === label,
  )
  if (!el) throw new Error(`找不到按鈕 ${label}`)
  return new DOMWrapper(el)
}

function escape(): void {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
}

function scrim(): DOMWrapper<Element> {
  const el = document.body.querySelector('.dialog-scrim')
  if (!el) throw new Error('找不到 scrim')
  return new DOMWrapper(el)
}

afterEach(() => {
  for (const w of mounted) w.unmount()
  mounted = []
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

describe('ConfirmDialog', () => {
  it('ConfirmDialog confirm and cancel emits', async () => {
    const wrapper = mountDialog()
    await button('確定取消').trigger('click')
    expect(wrapper.emitted('confirm')).toHaveLength(1)
    expect(wrapper.emitted('cancel')).toBeUndefined()
    await button('取消').trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('ConfirmDialog focuses cancel first', async () => {
    const wrapper = mountDialog({ open: false })
    await wrapper.setProps({ open: true })
    await nextTick()
    expect(document.activeElement?.textContent?.trim()).toBe('取消')
  })

  it('ConfirmDialog loading prevents double confirm', async () => {
    const wrapper = mountDialog({ loading: true })
    const confirm = button('確定取消')
    expect(confirm.attributes('aria-disabled')).toBe('true')
    expect(confirm.attributes('aria-busy')).toBe('true')
    await confirm.trigger('click')
    expect(wrapper.emitted('confirm')).toBeUndefined()
  })

  it('ConfirmDialog escape cancels', async () => {
    const wrapper = mountDialog()
    escape()
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    await wrapper.setProps({ open: false })
    expect(alertdialog()).toBeNull()
  })

  it('ConfirmDialog loading blocks cancel', async () => {
    const wrapper = mountDialog({ loading: true })
    expect(button('取消').attributes('disabled')).toBeDefined()
    escape()
    await scrim().trigger('click')
    expect(wrapper.emitted('cancel')).toBeUndefined()
    await wrapper.setProps({ loading: false })
    escape()
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('ConfirmDialog scrim cancels', async () => {
    const wrapper = mountDialog({ loading: false })
    await scrim().trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    expect(wrapper.emitted('confirm')).toBeUndefined()
  })

  it('ConfirmDialog renders alertdialog semantics with title and message', () => {
    mountDialog({ message: '取消後需要重新通知老師。\n確定嗎？' })
    const el = alertdialog()
    expect(el?.getAttribute('aria-modal')).toBe('true')
    expect(document.getElementById(el?.getAttribute('aria-labelledby') ?? '')?.textContent?.trim()).toBe('取消接送？')
    const described = document.getElementById(el?.getAttribute('aria-describedby') ?? '')
    expect(described?.textContent).toContain('取消後需要重新通知老師。')
  })

  it('ConfirmDialog without message has no description paragraph or aria-describedby', () => {
    mountDialog()
    expect(alertdialog()?.hasAttribute('aria-describedby')).toBe(false)
    expect(document.body.querySelector('.dialog__message')).toBeNull()
  })

  it('ConfirmDialog uses default labels and custom cancel label', async () => {
    const wrapper = mountDialog({ confirmLabel: undefined })
    expect(button('確定').exists()).toBe(true)
    expect(button('取消').exists()).toBe(true)
    await wrapper.setProps({ cancelLabel: '先不要' })
    expect(button('先不要').exists()).toBe(true)
  })

  it('ConfirmDialog destructive marks confirm button', async () => {
    const wrapper = mountDialog()
    expect(button('確定取消').classes()).not.toContain('is-destructive')
    await wrapper.setProps({ destructive: true })
    expect(button('確定取消').classes()).toContain('is-destructive')
    expect(button('取消').classes()).not.toContain('is-destructive')
  })

  it('ConfirmDialog renders default slot between message and actions', () => {
    mountDialog({ message: '說明' }, { default: '<label><input type="checkbox"> 下次不再提醒</label>' })
    const dialog = alertdialog()
    expect(dialog?.textContent).toContain('下次不再提醒')
    const html = dialog?.innerHTML ?? ''
    expect(html.indexOf('說明')).toBeLessThan(html.indexOf('下次不再提醒'))
    expect(html.indexOf('下次不再提醒')).toBeLessThan(html.indexOf('確定取消'))
  })

  it('ConfirmDialog traps Tab inside the dialog', async () => {
    mountDialog()
    await nextTick()
    const cancel = button('取消').element as HTMLElement
    const confirm = button('確定取消').element as HTMLElement
    confirm.focus()
    const forward = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    confirm.dispatchEvent(forward)
    expect(forward.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(cancel)
    const backward = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })
    cancel.dispatchEvent(backward)
    expect(document.activeElement).toBe(confirm)
  })

  it('ConfirmDialog restores focus and body scroll after closing', async () => {
    document.body.style.overflow = 'auto'
    const trigger = document.createElement('button')
    document.body.appendChild(trigger)
    trigger.focus()
    const wrapper = mountDialog({ open: false })
    await wrapper.setProps({ open: true })
    await nextTick()
    expect(document.body.style.overflow).toBe('hidden')
    await wrapper.setProps({ open: false })
    expect(document.body.style.overflow).toBe('auto')
    expect(document.activeElement).toBe(trigger)
  })

  it('ConfirmDialog above a bottom sheet takes Esc and keeps the sheet locked', async () => {
    document.body.style.overflow = 'auto'
    const sheet = mount(ParentBottomSheet, {
      attachTo: document.body,
      props: { modelValue: true, title: '接送碼' },
      slots: { default: '<button type="button">我已記下</button>' },
    })
    mounted.push(sheet)
    const dialog = mountDialog()
    await nextTick()
    escape()
    expect(dialog.emitted('cancel')).toHaveLength(1)
    expect(sheet.emitted('update:modelValue')).toBeUndefined()
    expect(alertdialog()?.contains(document.activeElement)).toBe(true)
    await dialog.setProps({ open: false })
    expect(document.body.style.overflow).toBe('hidden')
    escape()
    expect(sheet.emitted('update:modelValue')?.[0]).toEqual([false])
  })

  it('ConfirmDialog style follows design decisions', () => {
    expect(source).toMatch(/position:\s*fixed/)
    expect(source).toMatch(/var\(--m3-surface-container-high\)/)
    expect(source).toMatch(/var\(--m3-shape-extra-large\)/)
    expect(source).toMatch(/opacity:\s*0\.32/)
    expect(source).toMatch(/min-width:\s*280px/)
    expect(source).toMatch(/max-width:\s*400px/)
    expect(source).toMatch(/\.is-destructive[^{]*\{[^}]*var\(--m3-error\)/)
    expect(source).toMatch(/prefers-reduced-motion/)
    // 高於 BottomSheet（10）、低於 M3Snackbar（20）
    const z = Number(/\.dialog-layer\s*\{[^}]*z-index:\s*(\d+)/.exec(source)?.[1])
    expect(z).toBeGreaterThan(10)
    expect(z).toBeLessThan(20)
  })
})
