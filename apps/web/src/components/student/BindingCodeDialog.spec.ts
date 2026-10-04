import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { mountWithApp } from '@/test/helpers'
import BindingCodeDialog from './BindingCodeDialog.vue'

const PROPS = {
  modelValue: true,
  code: 'K7M2Q9XP',
  expiresAt: '2026-10-09T08:00:00Z',
  guardianName: '林美麗',
  studentName: '王小明',
}
const CONFIRM_TEXT = '關閉後將無法再次查看此綁定碼'

let writeText: ReturnType<typeof vi.fn>
const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
}

async function mountDialog(props: Record<string, unknown> = {}) {
  const { wrapper } = await mountWithApp(BindingCodeDialog, { props: { ...PROPS, ...props } })
  await settle()
  return wrapper
}

function dialogEl(): HTMLElement {
  const el = document.body.querySelector<HTMLElement>('.el-dialog')
  if (!el) throw new Error('dialog 沒有出現')
  return el
}

async function clickIn(root: ParentNode, text: string): Promise<void> {
  const button = Array.from(root.querySelectorAll<HTMLButtonElement>('button'))
    .filter((b) => b.textContent?.trim() === text)
    .at(-1)
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

describe('BindingCodeDialog', () => {
  beforeEach(() => {
    writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  })

  afterEach(() => {
    if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard)
    else delete (navigator as { clipboard?: unknown }).clipboard
    document.body.innerHTML = ''
  })

  it('BindingCodeDialog shows grouped code and expiry', async () => {
    await mountDialog()

    const dialog = dialogEl()
    expect(dialog.querySelector('[data-test=binding-code]')?.textContent?.trim()).toBe('K7M2-Q9XP')
    expect(dialog.textContent).toContain(
      '請 林美麗 在 LINE 開啟家長端，輸入此綁定碼即可綁定 王小明。綁定碼有效至 2026/10/09 16:00，只會顯示這一次。',
    )
    const footerButtons = Array.from(dialog.querySelectorAll('.el-dialog__footer button')).map((b) =>
      b.textContent?.trim(),
    )
    expect(footerButtons).toEqual(['完成'])
  })

  it('BindingCodeDialog copies raw code', async () => {
    const wrapper = await mountDialog()

    await clickIn(dialogEl(), '複製')
    expect(writeText).toHaveBeenCalledTimes(1)
    expect(writeText).toHaveBeenCalledWith('K7M2Q9XP')
    expect(dialogEl().querySelector('[data-test=copy-button]')?.textContent?.trim()).toBe('已複製')

    await clickIn(dialogEl(), '完成')
    expect(closeEmits(wrapper)?.[0]).toEqual([false])
    expect(messageBoxText()).not.toContain(CONFIRM_TEXT)
  })

  it('BindingCodeDialog confirms closing without copy', async () => {
    const wrapper = await mountDialog()

    await clickIn(dialogEl(), '完成')
    expect(messageBoxText()).toContain(CONFIRM_TEXT)
    expect(closeEmits(wrapper)).toBeUndefined()

    await clickIn(document.body.querySelector('.el-message-box')!, '取消')
    expect(closeEmits(wrapper)).toBeUndefined()

    await clickIn(dialogEl(), '完成')
    await clickIn(document.body.querySelector('.el-message-box')!, '確定關閉')
    expect(closeEmits(wrapper)?.[0]).toEqual([false])
  })

  it('BindingCodeDialog shows hint when clipboard fails', async () => {
    writeText.mockImplementation(() => Promise.reject(new Error('denied')))
    await mountDialog()

    await clickIn(dialogEl(), '複製')

    expect(dialogEl().textContent).toContain('無法複製，請手動抄寫')
    expect(dialogEl().querySelector('[data-test=copy-button]')?.textContent?.trim()).toBe('複製')
  })

  it('BindingCodeDialog confirms on X and Esc, ignores overlay click', async () => {
    const wrapper = await mountDialog()
    dialogEl().querySelector<HTMLButtonElement>('.el-dialog__headerbtn')!.click()
    await settle()
    expect(messageBoxText()).toContain(CONFIRM_TEXT)
    expect(closeEmits(wrapper)).toBeUndefined()

    document.body.innerHTML = ''
    const esc = await mountDialog()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }))
    await settle()
    expect(messageBoxText()).toContain(CONFIRM_TEXT)
    expect(closeEmits(esc)).toBeUndefined()

    document.body.innerHTML = ''
    const overlayWrapper = await mountDialog()
    const overlay = document.body.querySelector<HTMLElement>('.el-overlay-dialog')!
    overlay.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    overlay.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    overlay.click()
    await settle()
    expect(closeEmits(overlayWrapper)).toBeUndefined()
    expect(messageBoxText()).not.toContain(CONFIRM_TEXT)
  })

  it('BindingCodeDialog removes the code from DOM once closed and resets copied state on reopen', async () => {
    const wrapper = await mountDialog()
    await clickIn(dialogEl(), '複製')

    await wrapper.setProps({ modelValue: false })
    await settle()
    expect(document.body.querySelector('[data-test=binding-code]')).toBeNull()

    await wrapper.setProps({ modelValue: true, code: 'AB3DEFGH' })
    await settle()
    expect(dialogEl().querySelector('[data-test=binding-code]')?.textContent?.trim()).toBe('AB3D-EFGH')
    expect(dialogEl().querySelector('[data-test=copy-button]')?.textContent?.trim()).toBe('複製')
  })
})
