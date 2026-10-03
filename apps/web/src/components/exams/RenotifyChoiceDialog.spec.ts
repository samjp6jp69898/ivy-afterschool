import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { mountWithApp } from '@/test/helpers'
import RenotifyChoiceDialog from './RenotifyChoiceDialog.vue'

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
}

async function mountDialog(pendingCount = 3) {
  const { wrapper } = await mountWithApp(RenotifyChoiceDialog, { props: { modelValue: true, pendingCount } })
  await settle()
  return wrapper
}

function dialogEl(): HTMLElement {
  const el = document.body.querySelector<HTMLElement>('.el-dialog')
  if (!el) throw new Error('dialog 沒有出現')
  return el
}

async function clickButton(text: string): Promise<void> {
  const button = Array.from(dialogEl().querySelectorAll<HTMLButtonElement>('button')).find(
    (b) => b.textContent?.trim() === text,
  )
  if (!button) throw new Error(`找不到按鈕「${text}」`)
  button.click()
  await settle()
}

function lastModel(wrapper: VueWrapper): unknown[] | undefined {
  return wrapper.emitted('update:modelValue')?.at(-1)
}

describe('RenotifyChoiceDialog', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('RenotifyChoiceDialog shows pending count', async () => {
    await mountDialog(3)

    expect(dialogEl().textContent).toContain('此考試已發布，修改分數會記錄於稽核紀錄。')
    expect(dialogEl().textContent).toContain('是否在修改後通知家長分數已更新？')
    expect(dialogEl().textContent).toContain('目前有 3 格等待儲存')
  })

  it('RenotifyChoiceDialog emits choices', async () => {
    const notify = await mountDialog()
    await clickButton('通知家長')
    expect(notify.emitted('choose')?.[0]).toEqual([true])

    document.body.innerHTML = ''
    const silent = await mountDialog()
    await clickButton('不通知')
    expect(silent.emitted('choose')?.[0]).toEqual([false])
    expect(lastModel(silent)).toEqual([false])
  })

  it('RenotifyChoiceDialog choosing also closes', async () => {
    const wrapper = await mountDialog()

    await clickButton('通知家長')

    expect(lastModel(wrapper)).toEqual([false])
  })

  it('RenotifyChoiceDialog escape and close keep waiting', async () => {
    const esc = await mountDialog()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }))
    await settle()
    expect(esc.emitted('choose')).toBeUndefined()
    expect(lastModel(esc)).toEqual([false])

    document.body.innerHTML = ''
    const x = await mountDialog()
    dialogEl().querySelector<HTMLButtonElement>('.el-dialog__headerbtn')!.click()
    await settle()
    expect(x.emitted('choose')).toBeUndefined()
    expect(lastModel(x)).toEqual([false])
  })

  it('RenotifyChoiceDialog title footer and overlay', async () => {
    const wrapper = await mountDialog()

    expect(dialogEl().querySelector('.el-dialog__title')?.textContent?.trim()).toBe('修改已發布的成績')
    expect(
      Array.from(dialogEl().querySelectorAll('.el-dialog__footer button')).map((b) => b.textContent?.trim()),
    ).toEqual(['不通知', '通知家長'])
    expect(dialogEl().textContent).toContain('本次編輯期間都會套用此選擇，可在工具列切換')

    const overlay = document.body.querySelector<HTMLElement>('.el-overlay-dialog') ?? document.body.querySelector<HTMLElement>('.el-overlay')
    overlay!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    overlay!.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    overlay!.click()
    await settle()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(wrapper.emitted('choose')).toBeUndefined()
  })
})
