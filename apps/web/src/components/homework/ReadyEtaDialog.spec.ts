import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { mountWithApp } from '@/test/helpers'
import ReadyEtaDialog from './ReadyEtaDialog.vue'

const EMPTY_PROGRESS = {
  ready_eta: null,
  note: null,
  eta_updated_at: null,
  eta_updated_by_name: null,
}

// 台北 16:07
const NOW = new Date('2026-10-02T08:07:00Z')

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
}

async function mountDialog(props: Record<string, unknown> = {}): Promise<VueWrapper> {
  const { wrapper } = await mountWithApp(ReadyEtaDialog, {
    props: {
      modelValue: true,
      studentName: '王小明',
      progress: EMPTY_PROGRESS,
      loading: false,
      error: null,
      ...props,
    },
  })
  await settle()
  return wrapper
}

function dialogEl(): HTMLElement {
  const el = document.body.querySelector<HTMLElement>('.el-dialog')
  if (!el) throw new Error('dialog 沒有出現')
  return el
}

function buttonIn(text: string): HTMLButtonElement | undefined {
  return Array.from(dialogEl().querySelectorAll<HTMLButtonElement>('button'))
    .filter((b) => b.textContent?.trim() === text)
    .at(-1)
}

async function click(text: string): Promise<void> {
  const button = buttonIn(text)
  if (!button) throw new Error(`找不到按鈕「${text}」`)
  button.click()
  await settle()
}

function timeValue(): string {
  const shown = dialogEl().querySelector('.el-select .el-select__placeholder')
  if (!shown) throw new Error('找不到時間欄')
  return shown.textContent?.trim() ?? ''
}

function submitted(wrapper: VueWrapper): unknown[] {
  return (wrapper.emitted('submit') ?? []).map((args) => args[0])
}

describe('ReadyEtaDialog', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
  })

  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('ReadyEtaDialog quick buttons add minutes from now', async () => {
    const wrapper = await mountDialog()
    expect(dialogEl().querySelector('.el-dialog__title')?.textContent?.trim()).toBe('預計可接送時間：王小明')

    await click('+30 分')
    expect(timeValue()).toBe('16:40')

    await click('儲存')
    expect(submitted(wrapper)).toEqual([{ ready_eta: '16:40', note: null }])
  })

  it('ReadyEtaDialog quick buttons +15 and +60 round up to 5 minutes', async () => {
    await mountDialog()

    await click('+15 分')
    expect(timeValue()).toBe('16:25')
    await click('+60 分')
    expect(timeValue()).toBe('17:10')
  })

  it('ReadyEtaDialog disables quick buttons that would pass 21:00', async () => {
    vi.setSystemTime(new Date('2026-10-02T12:40:00Z')) // 台北 20:40
    await mountDialog()

    expect(buttonIn('+15 分')?.disabled).toBe(false)
    expect(buttonIn('+30 分')?.disabled).toBe(true)
    expect(buttonIn('+60 分')?.disabled).toBe(true)
  })

  it('ReadyEtaDialog clears eta but keeps note', async () => {
    const wrapper = await mountDialog({
      progress: { ...EMPTY_PROGRESS, ready_eta: '17:30', note: '訂正中' },
    })
    expect(timeValue()).toBe('17:30')

    await click('清除預計時間')
    expect(timeValue()).toBe('選擇時間')
    expect(buttonIn('清除預計時間')).toBeUndefined()
    await click('儲存')

    expect(submitted(wrapper)).toEqual([{ ready_eta: null, note: '訂正中' }])
  })

  it('ReadyEtaDialog sends trimmed note and null for blank note', async () => {
    const wrapper = await mountDialog({ progress: { ...EMPTY_PROGRESS, ready_eta: '17:30' } })
    const textarea = dialogEl().querySelector<HTMLTextAreaElement>('textarea')!
    expect(textarea.getAttribute('placeholder')).toBe('例如：數學訂正中，預計 17:30 完成')
    expect(textarea.getAttribute('maxlength')).toBe('100')

    textarea.value = '  數學訂正中  '
    textarea.dispatchEvent(new Event('input'))
    await settle()
    await click('儲存')
    textarea.value = '   '
    textarea.dispatchEvent(new Event('input'))
    await settle()
    await click('儲存')

    expect(submitted(wrapper)).toEqual([
      { ready_eta: '17:30', note: '數學訂正中' },
      { ready_eta: '17:30', note: null },
    ])
  })

  it('ReadyEtaDialog shows last updater and notice', async () => {
    await mountDialog({
      progress: {
        ...EMPTY_PROGRESS,
        ready_eta: '17:30',
        eta_updated_by_name: '陳老師',
        eta_updated_at: '2026-10-02T08:10:00Z',
      },
    })

    const text = dialogEl().textContent ?? ''
    expect(text).toContain('上次由 陳老師 於 16:10 設定')
    expect(text).toContain('儲存後會通知家長')
  })

  it('ReadyEtaDialog hides last updater when never set', async () => {
    await mountDialog()

    const text = dialogEl().textContent ?? ''
    expect(text).not.toContain('上次由')
    expect(text).toContain('儲存後會通知家長')
  })

  it('ReadyEtaDialog disables save when unchanged', async () => {
    await mountDialog({ progress: { ...EMPTY_PROGRESS, ready_eta: '17:30', note: '訂正中' } })
    expect(buttonIn('儲存')?.disabled).toBe(true)

    await click('+15 分')
    expect(buttonIn('儲存')?.disabled).toBe(false)
  })

  it('ReadyEtaDialog resets form from progress on reopen', async () => {
    const wrapper = await mountDialog({ progress: { ...EMPTY_PROGRESS, ready_eta: '17:30' } })
    await click('+15 分')
    expect(timeValue()).toBe('16:25')

    await wrapper.setProps({ modelValue: false })
    await settle()
    await wrapper.setProps({ modelValue: true })
    await settle()

    expect(timeValue()).toBe('17:30')
    expect(buttonIn('儲存')?.disabled).toBe(true)
  })

  it('ReadyEtaDialog shows error alert', async () => {
    await mountDialog({ error: '預計時間不可早於現在' })

    expect(dialogEl().querySelector('.el-alert')?.textContent).toContain('預計時間不可早於現在')
  })
})
