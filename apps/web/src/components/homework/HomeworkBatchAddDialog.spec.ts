import { DOMWrapper, flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { adminHttp } from '@/api/http'
import { createApiMock, mountWithApp } from '@/test/helpers'
import HomeworkBatchAddDialog from './HomeworkBatchAddDialog.vue'
import batchDialogSource from './HomeworkBatchAddDialog.vue?raw'

// GET /admin/subjects?active_only=true（lookups store）
const SUBJECTS = [
  { id: 'sub1', name: '國語', sort_order: 1, is_active: true },
  { id: 'sub2', name: '數學', sort_order: 2, is_active: true },
]
const RECENT_KEY = 'homework.recentTitles'

/** 看板上該班學生（BoardStudent 的子集） */
const PRESENT = { student_id: 's1', name: '王小明', attendance_status: 'present' }
const ON_LEAVE = { student_id: 's2', name: '張語晴', attendance_status: 'leave' }
const ABSENT = { student_id: 's3', name: '黃子軒', attendance_status: 'absent' }
const EXPECTED = { student_id: 's4', name: '劉宇恩', attendance_status: 'expected' }
const NO_RECORD = { student_id: 's5', name: '李承恩', attendance_status: null }

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
  const { wrapper } = await mountWithApp(HomeworkBatchAddDialog, {
    props: {
      modelValue: true,
      className: '中年級班',
      students: [PRESENT, ON_LEAVE, ABSENT],
      loading: false,
      error: null,
      ...props,
    },
  })
  await settle()
  return wrapper as VueWrapper
}

function dialog(): DOMWrapper<Element> {
  const el = Array.from(document.body.querySelectorAll('.el-dialog')).at(-1)
  if (!el) throw new Error('dialog 沒有出現')
  return new DOMWrapper(el)
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

async function chooseTarget(label: '全班' | '指定學生'): Promise<void> {
  const radio = formItem('對象')
    .findAll('.el-radio')
    .find((r) => r.find('.el-radio__label').text().startsWith(label))
  if (!radio) throw new Error(`找不到對象選項 ${label}`)
  await radio.find('input').setValue(true)
  await settle()
}

function studentBoxes(): DOMWrapper<Element>[] {
  return dialog().findAll('.homework-batch__students .el-checkbox')
}

function checkedNames(): string[] {
  return studentBoxes()
    .filter((b) => b.find<HTMLInputElement>('input').element.checked)
    .map((b) => b.text())
}

async function toggleStudent(name: string, on: boolean): Promise<void> {
  const box = studentBoxes().find((b) => b.text().startsWith(name))
  if (!box) throw new Error(`找不到學生 ${name}`)
  await box.find('input').setValue(on)
  await settle()
}

function submitButton(): DOMWrapper<Element> {
  const btn = dialog().findAll('.el-dialog__footer button').at(-1)
  if (!btn) throw new Error('找不到送出鈕')
  return btn
}

async function clickButton(text: string): Promise<void> {
  const btn = dialog()
    .findAll('button')
    .find((b) => b.text() === text)
  if (!btn) throw new Error(`找不到按鈕「${text}」`)
  await btn.trigger('click')
  await settle()
}

async function submit(): Promise<void> {
  await submitButton().trigger('click')
  await settle()
}

function submitted(wrapper: VueWrapper): unknown[] {
  return (wrapper.emitted('submit') ?? []).map((args) => args[0])
}

describe('HomeworkBatchAddDialog', () => {
  beforeEach(() => {
    mock = createApiMock(adminHttp)
    mock.onGet('/admin/subjects').reply(200, SUBJECTS)
  })

  afterEach(() => {
    mock.restore()
    document.body.innerHTML = ''
  })

  it('HomeworkBatchAddDialog whole class omits student ids', async () => {
    const wrapper = await mountDialog()
    expect(dialog().find('.el-dialog__title').text()).toBe('整班新增作業：中年級班')
    // FormDialog md（560px）
    expect(dialog().attributes('style')).toContain('560px')
    expect(titleInput().attributes('placeholder')).toBe('例如：國語第 5 課生字')
    expect(titleInput().attributes('maxlength')).toBe('100')
    // 內容必填且有字數計數
    expect(formItem('內容').classes()).toContain('is-required')
    expect(formItem('內容').find('.el-input__count').exists()).toBe(true)

    await chooseTarget('全班')
    await chooseSubject('國語')
    await titleInput().setValue(' 國語第 5 課生字 ')
    await submit()

    const bodies = submitted(wrapper)
    expect(bodies).toHaveLength(1)
    expect(bodies[0]).toStrictEqual({ subject_id: 'sub1', title: '國語第 5 課生字' })
  })

  it('HomeworkBatchAddDialog preselects present students', async () => {
    const wrapper = await mountDialog()
    expect(studentBoxes()).toHaveLength(0)

    await chooseTarget('指定學生')
    expect(studentBoxes().map((b) => b.text())).toEqual(['王小明', '張語晴（請假）', '黃子軒（缺席）'])
    expect(checkedNames()).toEqual(['王小明'])

    await titleInput().setValue('圈詞')
    await submit()

    expect(submitted(wrapper)).toStrictEqual([{ subject_id: null, title: '圈詞', student_ids: ['s1'] }])
  })

  it('HomeworkBatchAddDialog requires at least one student', async () => {
    const wrapper = await mountDialog()
    await chooseTarget('指定學生')
    await titleInput().setValue('圈詞')
    await toggleStudent('王小明', false)
    expect(checkedNames()).toEqual([])

    await submit()
    await settleErrors()

    expect(formItem('對象').text()).toContain('請至少選擇一位學生')
    expect(wrapper.emitted('submit')).toBeUndefined()

    await toggleStudent('黃子軒', true)
    await settleErrors()
    expect(formItem('對象').text()).not.toContain('請至少選擇一位學生')
  })

  it('HomeworkBatchAddDialog shows backend error', async () => {
    const wrapper = await mountDialog()
    expect(dialog().find('.el-alert').exists()).toBe(false)

    await wrapper.setProps({ error: '此班級沒有可新增作業的學生' })
    await settle()

    const alert = dialog().find('.el-alert')
    expect(alert.text()).toContain('此班級沒有可新增作業的學生')
    const form = dialog().find('.el-form').element
    expect(alert.element.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
  })

  it('HomeworkBatchAddDialog counts whole class including leave and absent', async () => {
    await mountDialog()

    await chooseTarget('全班')
    expect(submitButton().text()).toBe('新增給 3 位學生')
    expect(formItem('對象').text()).toContain('（含今天請假 / 缺席 2 位）')

    await chooseTarget('指定學生')
    expect(submitButton().text()).toBe('新增給 1 位學生')
    await toggleStudent('張語晴', true)
    expect(submitButton().text()).toBe('新增給 2 位學生')
    document.body.innerHTML = ''

    await mountDialog({ students: [PRESENT, { ...ON_LEAVE, attendance_status: 'present' }] })
    expect(submitButton().text()).toBe('新增給 2 位學生')
    expect(formItem('對象').text()).not.toContain('含今天請假')
  })

  it('HomeworkBatchAddDialog sends null subject after clearing', async () => {
    const wrapper = await mountDialog()
    await chooseSubject('數學')

    // 清除 icon 要對根元素 .el-select 觸發 mouseenter 才會出現
    await formItem('科目').find('.el-select').trigger('mouseenter')
    await formItem('科目').find('.el-select__clear').trigger('click')
    await settle()
    await titleInput().setValue('圈詞')
    await submit()

    expect(submitted(wrapper)).toStrictEqual([{ subject_id: null, title: '圈詞' }])
  })

  it('HomeworkBatchAddDialog requires title', async () => {
    const wrapper = await mountDialog()

    await titleInput().setValue('  ')
    await submit()
    await settleErrors()

    expect(formItem('內容').text()).toContain('請輸入作業內容')
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('HomeworkBatchAddDialog select all and only present students', async () => {
    const wrapper = await mountDialog({ students: [PRESENT, ON_LEAVE, ABSENT, EXPECTED, NO_RECORD] })
    await chooseTarget('指定學生')
    expect(checkedNames()).toEqual(['王小明', '劉宇恩（預計到班）', '李承恩'])

    await clickButton('全選')
    expect(submitButton().text()).toBe('新增給 5 位學生')

    await clickButton('只選今天到班的')
    expect(checkedNames()).toEqual(['王小明', '劉宇恩（預計到班）', '李承恩'])
    expect(submitButton().text()).toBe('新增給 3 位學生')

    // 送出的 student_ids 依清單順序，與勾選順序無關
    await toggleStudent('黃子軒', true)
    await toggleStudent('王小明', false)
    await toggleStudent('王小明', true)
    await titleInput().setValue('圈詞')
    await submit()

    expect(submitted(wrapper)).toStrictEqual([
      { subject_id: null, title: '圈詞', student_ids: ['s1', 's3', 's4', 's5'] },
    ])
  })

  it('HomeworkBatchAddDialog student list is a scrolling three-column touch grid', async () => {
    await mountDialog()
    await chooseTarget('指定學生')
    expect(dialog().find('.el-checkbox-group.homework-batch__students').exists()).toBe(true)

    // happy-dom 不算版面：鎖住稿的三欄、限高捲動與 44px 觸控高度
    const style = (batchDialogSource.match(/<style scoped>([\s\S]*?)<\/style>/)?.[1] ?? '').replace(/\s+/g, ' ')
    expect(style).toMatch(
      /\.homework-batch__students \{[^}]*grid-template-columns: repeat\(3, 1fr\);[^}]*max-height: 220px;[^}]*overflow: auto;/,
    )
    expect(style).toMatch(/\.homework-batch__students :deep\(\.el-checkbox\) \{[^}]*height: 44px;/)
  })

  it('HomeworkBatchAddDialog quick fills recent titles', async () => {
    localStorage.setItem(RECENT_KEY, '["數學習作 p.12-13","圈詞"]')
    await mountDialog()

    const chips = formItem('內容').findAll('.homework-recent .el-tag')
    expect(formItem('內容').find('.homework-recent').text()).toContain('最近使用')
    expect(chips.map((c) => c.text())).toEqual(['數學習作 p.12-13', '圈詞'])
    await chips[1]!.trigger('click')

    expect(titleInput().element.value).toBe('圈詞')
  })

  it('HomeworkBatchAddDialog remembers submitted title', async () => {
    localStorage.setItem(RECENT_KEY, '["數學習作 p.12-13","圈詞"]')
    const wrapper = await mountDialog()

    await titleInput().setValue('圈詞')
    await submit()

    expect(submitted(wrapper)).toStrictEqual([{ subject_id: null, title: '圈詞' }])
    expect(JSON.parse(localStorage.getItem(RECENT_KEY) ?? 'null')).toEqual(['圈詞', '數學習作 p.12-13'])
  })

  it('HomeworkBatchAddDialog resets target on reopen', async () => {
    const wrapper = await mountDialog()
    await chooseTarget('指定學生')
    await toggleStudent('張語晴', true)
    await titleInput().setValue('圈詞')

    await wrapper.setProps({ modelValue: false })
    await settle()
    await wrapper.setProps({ modelValue: true, students: [PRESENT, ON_LEAVE, ABSENT, EXPECTED] })
    await settle()

    expect(titleInput().element.value).toBe('')
    expect(submitButton().text()).toBe('新增給 4 位學生')
    await chooseTarget('指定學生')
    expect(checkedNames()).toEqual(['王小明', '劉宇恩（預計到班）'])
  })
})
