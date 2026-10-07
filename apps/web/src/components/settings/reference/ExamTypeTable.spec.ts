import { DOMWrapper, flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { adminHttp } from '@/api/http'
import FormDialog from '@/components/common/FormDialog.vue'
import { useLookupsStore } from '@/stores/lookups'
import { createApiMock, mountWithApp } from '@/test/helpers'
import ExamTypeTable from './ExamTypeTable.vue'

const LIST_URL = '/admin/exam-types'
const EXAM_TYPES = [
  { id: 't2', name: '小考', sort_order: 2, is_active: true },
  { id: 't1', name: '段考', sort_order: 1, is_active: true },
]
const WRITER = ['settings:read', 'settings:write']
const READER = ['settings:read']

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

async function mountTable(permissions: string[] = WRITER) {
  const mounted = await mountWithApp(ExamTypeTable, {
    piniaInitialState: {
      auth: {
        status: 'authenticated',
        user: {
          id: 'u1',
          username: 'director01',
          display_name: '張主任',
          role: { id: 'r-director', code: 'director', name: '主任' },
          permissions,
          must_change_password: false,
        },
      },
    },
  })
  await settle()
  return { wrapper: mounted.wrapper as VueWrapper, lookups: useLookupsStore(mounted.pinia) }
}

function rows(wrapper: VueWrapper): DOMWrapper<Element>[] {
  return wrapper.findAll('.el-table__body tr.el-table__row')
}

function rowOf(wrapper: VueWrapper, name: string): DOMWrapper<Element> {
  const row = rows(wrapper).find((r) => r.findAll('td')[0]?.text() === name)
  if (!row) throw new Error(`找不到列 ${name}`)
  return row
}

function buttonsText(root: VueWrapper | DOMWrapper<Element>): string[] {
  return root.findAll('button').map((b) => b.text())
}

async function clickIn(root: VueWrapper | DOMWrapper<Element>, text: string): Promise<void> {
  const btn = root.findAll('button').find((b) => b.text() === text)
  if (!btn) throw new Error(`找不到按鈕「${text}」`)
  await btn.trigger('click')
  await settle()
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

async function clickInMessageBox(text: string): Promise<void> {
  const box = Array.from(document.body.querySelectorAll<HTMLElement>('.el-message-box')).at(-1)
  const btn = Array.from(box?.querySelectorAll<HTMLButtonElement>('button') ?? []).find(
    (b) => b.textContent?.trim() === text,
  )
  if (!btn) throw new Error(`message box 找不到「${text}」`)
  btn.click()
  await settle()
}

function dialogOpen(wrapper: VueWrapper): boolean {
  return wrapper.findComponent(FormDialog).props('modelValue') as boolean
}

function listRequests(): number {
  return mock.history.get.filter((c) => c.url === LIST_URL).length
}

describe('ExamTypeTable', () => {
  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    document.body.innerHTML = ''
  })

  it('ExamTypeTable lists exam types', async () => {
    mock.onGet(LIST_URL).reply(200, EXAM_TYPES)
    const { wrapper } = await mountTable()

    expect(rows(wrapper).map((r) => r.findAll('td')[0]?.text())).toEqual(['段考', '小考'])
    expect(wrapper.findAll('.el-table__header th').map((th) => th.text())).toEqual(['名稱', '排序', '狀態', '操作'])
    expect(wrapper.find('.ref-toolbar__hint').text()).toBe(
      '建立考試時的類型下拉依排序顯示；停用的類型不會出現在新增資料的下拉選單',
    )
  })

  it('ExamTypeTable edits exam type', async () => {
    mock.onGet(LIST_URL).reply(200, EXAM_TYPES)
    mock.onPatch('/admin/exam-types/t1').reply(200, { id: 't1', name: '月考', sort_order: 1, is_active: true })
    const { wrapper, lookups } = await mountTable()

    await clickIn(rowOf(wrapper, '段考'), '編輯')
    expect(dialog().find('.el-dialog__title').text()).toBe('編輯考試類型')
    await formItem('名稱').find('input').setValue('月考')
    await clickIn(dialog(), '儲存')

    expect(mock.history.patch.map((c) => [c.url, JSON.parse(c.data as string)])).toEqual([
      ['/admin/exam-types/t1', { name: '月考', sort_order: 1, is_active: true }],
    ])
    expect(dialogOpen(wrapper)).toBe(false)
    expect(listRequests()).toBe(2)
    expect(lookups.invalidate).toHaveBeenCalledWith('examTypes')
  })

  it('ExamTypeTable creates exam type with next sort order', async () => {
    mock.onGet(LIST_URL).reply(200, EXAM_TYPES)
    mock.onPost(LIST_URL).reply(201, { id: 't3', name: '複習考', sort_order: 3, is_active: true })
    const { wrapper, lookups } = await mountTable()

    await clickIn(wrapper.find('.ref-toolbar'), '新增考試類型')
    expect(dialog().find('.el-dialog__title').text()).toBe('新增考試類型')
    expect(formItem('排序').find<HTMLInputElement>('input').element.value).toBe('3')
    await formItem('名稱').find('input').setValue('複習考')
    await clickIn(dialog(), '儲存')

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({ name: '複習考', sort_order: 3, is_active: true })
    expect(lookups.invalidate).toHaveBeenCalledWith('examTypes')
  })

  it('ExamTypeTable shows field error from 422', async () => {
    mock.onGet(LIST_URL).reply(200, EXAM_TYPES)
    mock.onPost(LIST_URL).reply(422, {
      error: {
        code: 'validation_error',
        message: '輸入資料格式錯誤',
        details: [{ loc: ['body', 'name'], msg: '名稱不可空白', type: 'value_error' }],
      },
    })
    const { wrapper } = await mountTable()

    await clickIn(wrapper.find('.ref-toolbar'), '新增考試類型')
    await formItem('名稱').find('input').setValue('複習考')
    await clickIn(dialog(), '儲存')
    await settleErrors()

    expect(formItem('名稱').find('.el-form-item__error').text()).toBe('名稱不可空白')
    expect(dialogOpen(wrapper)).toBe(true)
  })

  it('ExamTypeTable shows duplicate name under name field', async () => {
    mock.onGet(LIST_URL).reply(200, EXAM_TYPES)
    mock
      .onPost(LIST_URL)
      .reply(409, { error: { code: 'exam_type_name_taken', message: '名稱已存在', details: null } })
    const { wrapper } = await mountTable()

    await clickIn(wrapper.find('.ref-toolbar'), '新增考試類型')
    await formItem('名稱').find('input').setValue('段考')
    await clickIn(dialog(), '儲存')
    await settleErrors()

    expect(formItem('名稱').find('.el-form-item__error').text()).toBe('名稱已存在')
    expect(dialogOpen(wrapper)).toBe(true)
  })

  it('ExamTypeTable deletes with referenced and unreferenced messages', async () => {
    mock.onGet(LIST_URL).reply(200, EXAM_TYPES)
    mock.onDelete('/admin/exam-types/t1').reply(200, { deleted: false, deactivated: true })
    mock.onDelete('/admin/exam-types/t2').reply(200, { deleted: true, deactivated: false })
    const { wrapper, lookups } = await mountTable()

    await clickIn(rowOf(wrapper, '段考'), '刪除')
    expect(document.body.querySelector('.el-message-box')?.textContent).toContain('確定要刪除考試類型「段考」嗎？')
    await clickInMessageBox('刪除')
    expect(document.body.textContent).toContain('「段考」已有資料引用，已改為停用')

    await clickIn(rowOf(wrapper, '小考'), '刪除')
    await clickInMessageBox('刪除')
    expect(document.body.textContent).toContain('已刪除')
    expect(mock.history.delete.map((c) => c.url)).toEqual(['/admin/exam-types/t1', '/admin/exam-types/t2'])
    expect(listRequests()).toBe(3)
    expect(lookups.invalidate).toHaveBeenCalledWith('examTypes')
  })

  it('ExamTypeTable hides write actions without permission', async () => {
    mock.onGet(LIST_URL).reply(200, EXAM_TYPES)
    const { wrapper } = await mountTable(READER)

    expect(rows(wrapper)).toHaveLength(2)
    expect(buttonsText(wrapper)).toEqual([])
    expect(wrapper.findAll('.el-table__header th').map((th) => th.text())).toEqual(['名稱', '排序', '狀態'])
  })

  it('ExamTypeTable empty and load error states', async () => {
    mock.onGet(LIST_URL).reply(200, [])
    const { wrapper } = await mountTable()
    expect(wrapper.text()).toContain('尚未建立考試類型')
    expect(buttonsText(wrapper.find('.empty-state'))).toEqual(['新增考試類型'])
    wrapper.unmount()
    document.body.innerHTML = ''
    mock.reset()

    mock
      .onGet(LIST_URL)
      .replyOnce(500, { error: { code: 'internal_error', message: '伺服器錯誤', details: null } })
      .onGet(LIST_URL)
      .reply(200, EXAM_TYPES)
    const { wrapper: failed } = await mountTable()
    const error = failed.find('.empty-state.is-error')
    expect(error.text()).toContain('無法載入考試類型')
    await clickIn(error, '重試')
    expect(rows(failed)).toHaveLength(2)
  })
})
