import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { mountWithApp } from '@/test/helpers'
import TempPasswordDialog from './TempPasswordDialog.vue'

const PROPS = { modelValue: true, tempPassword: 'Tmp-8f3K2q9x', username: 'tutor02', displayName: '陳老師' }
const CONFIRM_TEXT = '關閉後將無法再次查看此密碼'

let writeText: ReturnType<typeof vi.fn>
const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
}

async function mountDialog(props: Record<string, unknown> = {}) {
  const { wrapper } = await mountWithApp(TempPasswordDialog, { props: { ...PROPS, ...props } })
  await settle()
  return wrapper
}

function dialogEl(): HTMLElement {
  const el = document.body.querySelector<HTMLElement>('.el-dialog')
  if (!el) throw new Error('dialog 沒有出現')
  return el
}

async function clickIn(root: ParentNode, text: string): Promise<void> {
  const buttons = Array.from(root.querySelectorAll<HTMLButtonElement>('button')).filter(
    (b) => b.textContent?.trim() === text,
  )
  const button = buttons.at(-1)
  if (!button) throw new Error(`找不到按鈕「${text}」`)
  button.click()
  await settle()
}

function messageBoxText(): string {
  return Array.from(document.body.querySelectorAll('.el-message-box'))
    .map((el) => el.textContent ?? '')
    .join('')
}

function closeEmits(wrapper: VueWrapper): unknown[][] | undefined {
  return wrapper.emitted('update:modelValue')
}

describe('TempPasswordDialog', () => {
  beforeEach(() => {
    writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  })

  afterEach(() => {
    if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard)
    else delete (navigator as { clipboard?: unknown }).clipboard
    document.body.innerHTML = ''
  })

  it('TempPasswordDialog shows password and one-time notice', async () => {
    await mountDialog()

    const dialog = dialogEl()
    expect(dialog.querySelector('.el-dialog__title')?.textContent?.trim()).toBe('臨時密碼')
    expect(dialog.querySelector('[data-test=temp-password]')?.textContent?.trim()).toBe('Tmp-8f3K2q9x')
    expect(dialog.textContent).toContain('此密碼只會顯示這一次。陳老師（帳號 tutor02）首次登入後必須修改密碼。')
    const footerButtons = Array.from(dialog.querySelectorAll('.el-dialog__footer button')).map((b) =>
      b.textContent?.trim(),
    )
    expect(footerButtons).toEqual(['我已記下'])
  })

  it('TempPasswordDialog copies to clipboard', async () => {
    const wrapper = await mountDialog()

    await clickIn(dialogEl(), '複製')
    expect(writeText).toHaveBeenCalledWith('Tmp-8f3K2q9x')
    expect(document.body.textContent).toContain('已複製')

    await clickIn(dialogEl(), '我已記下')
    expect(closeEmits(wrapper)?.[0]).toEqual([false])
    expect(messageBoxText()).not.toContain(CONFIRM_TEXT)
  })

  it('TempPasswordDialog confirms before closing without copy', async () => {
    const wrapper = await mountDialog()

    await clickIn(dialogEl(), '我已記下')
    expect(messageBoxText()).toContain(CONFIRM_TEXT)

    await clickIn(document.body.querySelector('.el-message-box')!, '取消')
    expect(closeEmits(wrapper)).toBeUndefined()
  })

  it('TempPasswordDialog handles clipboard failure', async () => {
    writeText.mockImplementation(() => Promise.reject(new Error('denied')))
    await mountDialog()

    await clickIn(dialogEl(), '複製')

    expect(document.body.textContent).toContain('無法複製，請手動抄寫')
    expect(Array.from(dialogEl().querySelectorAll('button')).map((b) => b.textContent?.trim())).toContain('複製')
  })

  it('TempPasswordDialog confirms on X and Esc too', async () => {
    const wrapper = await mountDialog()
    dialogEl().querySelector<HTMLButtonElement>('.el-dialog__headerbtn')!.click()
    await settle()
    expect(messageBoxText()).toContain(CONFIRM_TEXT)
    await clickIn(document.body.querySelector('.el-message-box')!, '確定關閉')
    expect(closeEmits(wrapper)?.[0]).toEqual([false])

    document.body.innerHTML = ''
    const esc = await mountDialog()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }))
    await settle()
    expect(messageBoxText()).toContain(CONFIRM_TEXT)
    expect(closeEmits(esc)).toBeUndefined()
  })

  it('TempPasswordDialog ignores overlay click', async () => {
    const wrapper = await mountDialog()

    const overlay = document.body.querySelector<HTMLElement>('.el-overlay-dialog') ?? document.body.querySelector<HTMLElement>('.el-overlay')
    overlay!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    overlay!.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    overlay!.click()
    await settle()

    expect(closeEmits(wrapper)).toBeUndefined()
    expect(messageBoxText()).not.toContain(CONFIRM_TEXT)
  })

  it('TempPasswordDialog shows copy success text and resets on reopen', async () => {
    const wrapper = await mountDialog()
    await clickIn(dialogEl(), '複製')
    expect(dialogEl().querySelector('[data-test=copy-button]')?.textContent?.trim()).toBe('已複製')
    expect(document.body.textContent).toContain('已複製到剪貼簿，請交給本人。')

    await wrapper.setProps({ modelValue: false })
    await settle()
    expect(document.body.textContent).not.toContain('Tmp-8f3K2q9x')

    await wrapper.setProps({ modelValue: true, tempPassword: 'Tmp-Q7m2Lp4d' })
    await settle()
    expect(dialogEl().querySelector('[data-test=copy-button]')?.textContent?.trim()).toBe('複製')
    expect(document.body.textContent).not.toContain('已複製到剪貼簿')
    expect(dialogEl().querySelector('[data-test=temp-password]')?.textContent?.trim()).toBe('Tmp-Q7m2Lp4d')
  })
})
