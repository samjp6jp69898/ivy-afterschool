import { DOMWrapper, flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { adminHttp } from '@/api/http'
import { createApiMock, mountWithApp } from '@/test/helpers'
import HomeworkItemDialog from './HomeworkItemDialog.vue'

// GET /admin/subjects?active_only=true（lookups store）
const SUBJECTS = [
  { id: 'sub1', name: '國語', sort_order: 1, is_active: true },
  { id: 'sub2', name: '數學', sort_order: 2, is_active: true },
  { id: 'sub3', name: '英語', sort_order: 3, is_active: true },
]
const RECENT_KEY = 'homework.recentTitles'

let mock: MockAdapter

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
}

/** el-form-item 的錯誤訊息有 100ms debounce，斷言錯誤文字前等過 */
async function settleErrors(): Promise<void> {
  await settle()
  await new Promise((resolve) => setTimeout(resolve, 150))
  await settle()
}

async function mountDialog(props: Record<string, unknown> = {}): Promise<VueWrapper> {
  const { wrapper } = await mountWithApp(HomeworkItemDialog, {
    props: { modelValue: true, studentName: '王小明', loading: false, error: null, ...props },
  })
  await settle()
  return wrapper as VueWrapper
}

function dialog(): DOMWrapper<Element> {
  const el = Array.from(document.body.querySelectorAll('.el-dialog')).at(-1)
  if (!el) throw new Error('dialog 沒有出現')
  return new DOMWrapper(el)
}

function dialogTitle(): string {
  return dialog().find('.el-dialog__title').text()
}

function formItem(label: string): DOMWrapper<Element> {
  const item = dialog()
    .findAll('.el-form-item')
    .find((i) => i.find('.el-form-item__label').text().trim() === label)
  if (!item) throw new Error(`找不到欄位 ${label}`)
  return item
}

function titleInput(): DOMWrapper<HTMLInputElement> {
  return formItem('內容').find<HTMLInputElement>('input')
}

function subjectShown(): string {
  return formItem('科目').find('.el-select__placeholder').text()
}

async function chooseSubject(label: string): Promise<void> {
  const item = formItem('科目')
  await item.find('.el-select__wrapper').trigger('click')
  await settle()
  const listId = item.find('input').attributes('aria-controls')
  const options = Array.from(
    document.getElementById(listId!)?.querySelectorAll<HTMLElement>('.el-select-dropdown__item') ?? [],
  )
  const option = options.find((o) => o.textContent?.trim() === label)
  if (!option) throw new Error(`找不到科目選項 ${label}`)
  option.click()
  await settle()
}

function recentChips(): DOMWrapper<Element>[] {
  return formItem('內容').findAll('.homework-recent .el-tag')
}

async function save(): Promise<void> {
  const btn = dialog()
    .findAll('.el-dialog__footer button')
    .find((b) => b.text() === '儲存')
  if (!btn) throw new Error('找不到「儲存」')
  await btn.trigger('click')
  await settle()
}

function submitted(wrapper: VueWrapper): unknown[] {
  return (wrapper.emitted('submit') ?? []).map((args) => args[0])
}

function storedRecent(): unknown {
  return JSON.parse(localStorage.getItem(RECENT_KEY) ?? 'null')
}

describe('HomeworkItemDialog', () => {
  beforeEach(() => {
    mock = createApiMock(adminHttp)
    mock.onGet('/admin/subjects').reply(200, SUBJECTS)
  })

  afterEach(() => {
    mock.restore()
    document.body.innerHTML = ''
  })

  it('HomeworkItemDialog emits trimmed body', async () => {
    const wrapper = await mountDialog()
    expect(dialogTitle()).toBe('新增作業：王小明')
    // FormDialog sm（420px）
    expect(dialog().attributes('style')).toContain('420px')
    expect(titleInput().attributes('placeholder')).toBe('例如：數學習作 p.12-13')
    expect(titleInput().attributes('maxlength')).toBe('100')
    // 內容必填且有字數計數
    expect(formItem('內容').classes()).toContain('is-required')
    expect(formItem('內容').find('.el-input__count').exists()).toBe(true)

    await chooseSubject('數學')
    await titleInput().setValue(' 數學習作 p.12-13 ')
    await save()

    expect(submitted(wrapper)).toEqual([{ subject_id: 'sub2', title: '數學習作 p.12-13' }])
  })

  it('HomeworkItemDialog requires title', async () => {
    const wrapper = await mountDialog()

    await titleInput().setValue('   ')
    await save()
    await settleErrors()

    expect(formItem('內容').text()).toContain('請輸入作業內容')
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('HomeworkItemDialog sends null subject when none chosen', async () => {
    const wrapper = await mountDialog()
    expect(subjectShown()).toBe('不指定科目')

    await titleInput().setValue('圈詞')
    await save()

    expect(submitted(wrapper)).toEqual([{ subject_id: null, title: '圈詞' }])
  })

  it('HomeworkItemDialog sends null subject after clearing', async () => {
    const wrapper = await mountDialog()
    await chooseSubject('數學')
    expect(subjectShown()).toBe('數學')

    // 清除 icon 要對根元素 .el-select 觸發 mouseenter 才會出現
    await formItem('科目').find('.el-select').trigger('mouseenter')
    await formItem('科目').find('.el-select__clear').trigger('click')
    await settle()
    expect(subjectShown()).toBe('不指定科目')

    await titleInput().setValue('圈詞')
    await save()
    expect(submitted(wrapper)).toEqual([{ subject_id: null, title: '圈詞' }])
  })

  it('HomeworkItemDialog quick fills recent titles', async () => {
    localStorage.setItem(RECENT_KEY, '["國語第 5 課生字"]')
    await mountDialog()

    expect(formItem('內容').find('.homework-recent').text()).toContain('最近使用')
    expect(recentChips().map((c) => c.text())).toEqual(['國語第 5 課生字'])

    await recentChips()[0]!.trigger('click')

    expect(titleInput().element.value).toBe('國語第 5 課生字')
  })

  it('HomeworkItemDialog recent title chips work with keyboard', async () => {
    localStorage.setItem(RECENT_KEY, '["國語第 5 課生字","圈詞"]')
    await mountDialog()

    const chip = recentChips()[1]!
    expect(chip.attributes('role')).toBe('button')
    expect(chip.attributes('tabindex')).toBe('0')
    await chip.trigger('keydown', { key: 'Enter' })

    expect(titleInput().element.value).toBe('圈詞')
  })

  it('HomeworkItemDialog prefills when editing', async () => {
    const wrapper = await mountDialog({ item: { subject_id: 'sub1', subject_name: '國語', title: '圈詞' } })

    expect(dialogTitle()).toBe('編輯作業：王小明')
    expect(titleInput().element.value).toBe('圈詞')
    expect(subjectShown()).toBe('國語')

    // 沒有變更也照樣 emit，是否送請求由看板比對原值決定
    await save()
    expect(submitted(wrapper)).toEqual([{ subject_id: 'sub1', title: '圈詞' }])
  })

  it('HomeworkItemDialog keeps inactive subject of edited item', async () => {
    const wrapper = await mountDialog({ item: { subject_id: 'sub9', subject_name: '書法', title: '寫春聯' } })

    expect(subjectShown()).toBe('書法')
    await save()

    expect(submitted(wrapper)).toEqual([{ subject_id: 'sub9', title: '寫春聯' }])
  })

  it('HomeworkItemDialog shows error above form', async () => {
    const wrapper = await mountDialog()
    expect(dialog().find('.el-form').exists()).toBe(true)
    expect(dialog().find('.el-alert').exists()).toBe(false)

    await wrapper.setProps({ error: '作業日期超出可編輯的範圍' })
    await settle()

    const alert = dialog().find('.el-alert')
    expect(alert.text()).toContain('作業日期超出可編輯的範圍')
    const form = dialog().find('.el-form').element
    expect(alert.element.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
  })

  it('HomeworkItemDialog remembers submitted titles most recent first', async () => {
    localStorage.setItem(RECENT_KEY, JSON.stringify(['國語第 5 課生字', '圈詞', '英語單字抄寫', '數學圈圈看', '自然習作 p.8']))
    const wrapper = await mountDialog()

    await titleInput().setValue(' 英語單字抄寫 ')
    await save()
    expect(storedRecent()).toEqual(['英語單字抄寫', '國語第 5 課生字', '圈詞', '數學圈圈看', '自然習作 p.8'])

    await titleInput().setValue('數學習作 p.12-13')
    await save()
    expect(storedRecent()).toEqual(['數學習作 p.12-13', '英語單字抄寫', '國語第 5 課生字', '圈詞', '數學圈圈看'])
    expect(submitted(wrapper)).toHaveLength(2)
  })

  it('HomeworkItemDialog shows at most five recent titles', async () => {
    localStorage.setItem(
      RECENT_KEY,
      JSON.stringify(['國語第 5 課生字', '圈詞', 3, '', '英語單字抄寫', '圈詞', '數學圈圈看', '自然習作 p.8', '社會習作']),
    )
    await mountDialog()

    expect(recentChips().map((c) => c.text())).toEqual([
      '國語第 5 課生字',
      '圈詞',
      '英語單字抄寫',
      '數學圈圈看',
      '自然習作 p.8',
    ])
  })

  it('HomeworkItemDialog ignores unreadable recent titles', async () => {
    localStorage.setItem(RECENT_KEY, '{not json')
    await mountDialog()
    expect(formItem('內容').find('.homework-recent').exists()).toBe(false)
    document.body.innerHTML = ''

    const denied = (): never => {
      throw new Error('SecurityError')
    }
    vi.stubGlobal('localStorage', { getItem: denied, setItem: denied, removeItem: denied })
    const wrapper = await mountDialog()
    expect(formItem('內容').find('.homework-recent').exists()).toBe(false)

    await titleInput().setValue('圈詞')
    await save()
    expect(submitted(wrapper)).toEqual([{ subject_id: null, title: '圈詞' }])
  })

  it('HomeworkItemDialog resets form on reopen', async () => {
    const wrapper = await mountDialog({ item: { subject_id: 'sub1', subject_name: '國語', title: '圈詞' } })
    await chooseSubject('英語')
    await titleInput().setValue('英語單字抄寫')
    await save()

    await wrapper.setProps({ modelValue: false })
    await settle()
    await wrapper.setProps({ modelValue: true })
    await settle()

    expect(titleInput().element.value).toBe('圈詞')
    expect(subjectShown()).toBe('國語')
    // 重新開啟時重讀最近內容（含剛送出的那一筆）
    expect(recentChips().map((c) => c.text())).toEqual(['英語單字抄寫'])
  })
})
