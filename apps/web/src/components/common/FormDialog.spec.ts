import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { mountWithApp } from '@/test/helpers'
import FormDialog from './FormDialog.vue'

const DISCARD_TEXT = '尚未儲存的變更將會遺失，確定要關閉嗎？'

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
}

async function mountDialog(props: Record<string, unknown> = {}, slot = '<input id="name" />') {
  const { wrapper } = await mountWithApp(FormDialog, {
    props: { modelValue: true, title: '新增學生', ...props },
    slots: { default: slot },
  })
  await settle()
  return wrapper
}

function dialogEl(): HTMLElement {
  const el = document.body.querySelector<HTMLElement>('.el-dialog')
  if (!el) throw new Error('dialog 沒有出現')
  return el
}

function footerButtons(): HTMLButtonElement[] {
  return Array.from(dialogEl().querySelectorAll<HTMLButtonElement>('.el-dialog__footer button'))
}

function button(text: string): HTMLButtonElement {
  const b = footerButtons().find((x) => x.textContent?.trim() === text)
  if (!b) throw new Error(`找不到按鈕「${text}」`)
  return b
}

async function clickMessageBox(text: string): Promise<void> {
  await settle()
  const b = Array.from(document.body.querySelectorAll<HTMLButtonElement>('.el-message-box button')).find(
    (x) => x.textContent?.trim() === text,
  )
  if (!b) throw new Error(`確認框找不到按鈕「${text}」`)
  b.click()
  await settle()
}

function messageBoxText(): string {
  return Array.from(document.body.querySelectorAll('.el-message-box'))
    .map((el) => el.textContent ?? '')
    .join('')
}

async function pressEscape(): Promise<void> {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }))
  await settle()
}

async function clickOverlay(): Promise<void> {
  const overlay =
    document.body.querySelector<HTMLElement>('.el-overlay-dialog') ?? document.body.querySelector<HTMLElement>('.el-overlay')
  overlay!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
  overlay!.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
  overlay!.click()
  await settle()
}

function modelEmits(wrapper: VueWrapper): unknown[][] | undefined {
  return wrapper.emitted('update:modelValue')
}

describe('FormDialog', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('FormDialog emits submit and shows custom text', async () => {
    const wrapper = await mountDialog({ submitText: '建立' })

    button('建立').click()
    await settle()

    expect(wrapper.emitted('submit')).toHaveLength(1)
    expect(modelEmits(wrapper)).toBeUndefined()
    expect(dialogEl().querySelector('.el-dialog__title')?.textContent?.trim()).toBe('新增學生')
  })

  it('FormDialog disables actions while loading', async () => {
    const wrapper = await mountDialog({ loading: true })

    const submit = button('儲存')
    expect(submit.classList.contains('is-loading')).toBe(true)
    expect(submit.disabled).toBe(true)
    button('取消').click()
    await settle()

    expect(modelEmits(wrapper)).toBeUndefined()
  })

  it('FormDialog loading hides close and ignores escape', async () => {
    const wrapper = await mountDialog({ loading: true, dirty: true })

    expect(dialogEl().querySelector('.el-dialog__headerbtn')).toBeNull()
    expect(button('取消').disabled).toBe(true)
    await pressEscape()
    await clickOverlay()

    expect(modelEmits(wrapper)).toBeUndefined()
    expect(messageBoxText()).not.toContain(DISCARD_TEXT)
  })

  it('FormDialog confirms before closing dirty form', async () => {
    const wrapper = await mountDialog({ dirty: true })

    button('取消').click()
    await settle()
    expect(document.body.textContent).toContain(DISCARD_TEXT)
    await clickMessageBox('繼續編輯')
    expect(modelEmits(wrapper)).toBeUndefined()

    button('取消').click()
    await clickMessageBox('放棄變更')
    expect(modelEmits(wrapper)?.[0]).toEqual([false])
  })

  it('FormDialog closes directly when clean', async () => {
    const wrapper = await mountDialog({ dirty: false })

    button('取消').click()
    await settle()

    expect(modelEmits(wrapper)?.[0]).toEqual([false])
    expect(messageBoxText()).not.toContain(DISCARD_TEXT)
  })

  it('FormDialog closes via X Esc and overlay with dirty check', async () => {
    const x = await mountDialog()
    dialogEl().querySelector<HTMLButtonElement>('.el-dialog__headerbtn')!.click()
    await settle()
    expect(modelEmits(x)?.[0]).toEqual([false])

    document.body.innerHTML = ''
    const overlay = await mountDialog()
    await clickOverlay()
    expect(modelEmits(overlay)?.[0]).toEqual([false])

    document.body.innerHTML = ''
    const esc = await mountDialog({ dirty: true })
    await pressEscape()
    expect(messageBoxText()).toContain(DISCARD_TEXT)
    expect(modelEmits(esc)).toBeUndefined()
  })

  it('FormDialog footer order submitDisabled and hideFooter', async () => {
    await mountDialog({ submitDisabled: true })
    expect(footerButtons().map((b) => b.textContent?.trim())).toEqual(['取消', '儲存'])
    expect(button('儲存').disabled).toBe(true)
    expect(button('取消').disabled).toBe(false)

    document.body.innerHTML = ''
    await mountDialog({ hideFooter: true })
    expect(dialogEl().querySelector('.el-dialog__footer')).toBeNull()
  })

  it('FormDialog sizes and mobile fullscreen', async () => {
    for (const [size, width] of [
      ['sm', '420px'],
      ['md', '560px'],
      ['lg', '800px'],
    ] as const) {
      document.body.innerHTML = ''
      await mountDialog({ size })
      expect(dialogEl().getAttribute('style') ?? '', size).toContain(width)
      expect(dialogEl().classList.contains('is-fullscreen')).toBe(false)
    }

    document.body.innerHTML = ''
    vi.stubGlobal(
      'matchMedia',
      vi.fn((query: string) => ({
        matches: query === '(max-width: 767.98px)',
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    )
    await mountDialog()
    expect(dialogEl().classList.contains('is-fullscreen')).toBe(true)
  })

  it('FormDialog autofocus first editable field after opened', async () => {
    const slot = `
      <input id="locked" disabled />
      <div class="el-select"><input id="picker" /></div>
      <div class="el-date-editor"><input id="date" /></div>
      <input id="ro" readonly />
      <input id="agree" type="checkbox" />
      <input id="name" />
      <textarea id="note"></textarea>`
    const wrapper = await mountDialog({}, slot)

    wrapper.findComponent({ name: 'ElDialog' }).vm.$emit('opened')
    await settle()
    expect(document.activeElement?.id).toBe('name')

    wrapper.findComponent({ name: 'ElDialog' }).vm.$emit('closed')
    expect(wrapper.emitted('closed')).toHaveLength(1)

    document.body.innerHTML = ''
    ;(document.activeElement as HTMLElement | null)?.blur()
    const noFocus = await mountDialog({ autofocus: false })
    noFocus.findComponent({ name: 'ElDialog' }).vm.$emit('opened')
    await settle()
    expect(document.activeElement?.id).not.toBe('name')
  })
})
