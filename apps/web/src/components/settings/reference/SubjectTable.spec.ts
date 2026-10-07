import { DOMWrapper, flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { adminHttp } from '@/api/http'
import FormDialog from '@/components/common/FormDialog.vue'
import { useLookupsStore } from '@/stores/lookups'
import { createApiMock, mountWithApp } from '@/test/helpers'
import SubjectTable from './SubjectTable.vue'

const LIST_URL = '/admin/subjects'
const SUBJECTS = [
  { id: 's2', name: '數學', sort_order: 2, is_active: true },
  { id: 's1', name: '國語', sort_order: 1, is_active: true },
  { id: 's9', name: '作文', sort_order: 9, is_active: false },
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
  const mounted = await mountWithApp(SubjectTable, {
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

function headers(wrapper: VueWrapper): string[] {
  return wrapper.findAll('.el-table__header th').map((th) => th.text())
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

async function setSortOrder(value: string): Promise<void> {
  const input = formItem('排序').find('input')
  await input.setValue(value)
  await input.trigger('change')
  await settle()
}

async function saveDialog(): Promise<void> {
  await clickIn(dialog(), '儲存')
}

function lastMessageBox(): HTMLElement {
  const box = Array.from(document.body.querySelectorAll<HTMLElement>('.el-message-box')).at(-1)
  if (!box) throw new Error('message box 沒有出現')
  return box
}

async function clickInMessageBox(text: string): Promise<void> {
  const btn = Array.from(lastMessageBox().querySelectorAll<HTMLButtonElement>('button')).find(
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

describe('SubjectTable', () => {
  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    document.body.innerHTML = ''
  })

  it('SubjectTable lists subjects sorted with status', async () => {
    mock.onGet(LIST_URL).reply(200, SUBJECTS)
    const { wrapper } = await mountTable()

    expect(rows(wrapper).map((r) => r.findAll('td')[0]?.text())).toEqual(['國語', '數學', '作文'])
    expect(headers(wrapper)).toEqual(['名稱', '排序', '狀態', '操作'])
    expect(rowOf(wrapper, '作文').text()).toContain('停用')
    expect(rowOf(wrapper, '作文').classes()).toContain('ref-row-inactive')
    expect(rowOf(wrapper, '國語').find('.el-tag').text()).toBe('啟用')
    expect(rowOf(wrapper, '國語').find('.el-tag').classes()).toContain('el-tag--success')
    expect(rowOf(wrapper, '國語').findAll('td')[1]?.classes()).toContain('is-right')
    expect(wrapper.find('.ref-toolbar__hint').text()).toBe(
      '成績與作業的科目下拉依排序顯示；停用的科目不會出現在新增資料的下拉選單',
    )
  })

  it('SubjectTable creates subject via dialog', async () => {
    mock.onGet(LIST_URL).reply(200, SUBJECTS)
    mock.onPost(LIST_URL).reply(201, { id: 's4', name: '自然', sort_order: 4, is_active: true })
    const { wrapper, lookups } = await mountTable()

    await clickIn(wrapper.find('.ref-toolbar'), '新增科目')
    expect(dialog().find('.el-dialog__title').text()).toBe('新增科目')
    expect(dialog().attributes('style')).toContain('420px')
    await formItem('名稱').find('input').setValue('自然')
    await setSortOrder('4')
    await saveDialog()

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({ name: '自然', sort_order: 4, is_active: true })
    expect(dialogOpen(wrapper)).toBe(false)
    expect(listRequests()).toBe(2)
    expect(lookups.invalidate).toHaveBeenCalledWith('subjects')
  })

  it('SubjectTable defaults sort order and explains active switch', async () => {
    mock.onGet(LIST_URL).reply(200, SUBJECTS)
    const { wrapper } = await mountTable()

    await clickIn(wrapper.find('.ref-toolbar'), '新增科目')
    expect(formItem('排序').find<HTMLInputElement>('input').element.value).toBe('10')
    expect(formItem('排序').text()).toContain('數字小的排前面')
    expect(formItem('名稱').find('input').attributes('maxlength')).toBe('20')
    expect(formItem('啟用').text()).toContain('會出現在下拉選單')

    await formItem('啟用').find('.el-switch').trigger('click')
    await settle()
    expect(formItem('啟用').text()).toContain('不會出現在新增資料的下拉選單')
  })

  it('SubjectTable edits subject', async () => {
    mock.onGet(LIST_URL).reply(200, SUBJECTS)
    mock.onPatch('/admin/subjects/s1').reply(200, { id: 's1', name: '國文', sort_order: 1, is_active: true })
    const { wrapper } = await mountTable()

    await clickIn(rowOf(wrapper, '國語'), '編輯')
    expect(dialog().find('.el-dialog__title').text()).toBe('編輯科目')
    expect(formItem('名稱').find<HTMLInputElement>('input').element.value).toBe('國語')
    await formItem('名稱').find('input').setValue(' 國文 ')
    await saveDialog()

    expect(mock.history.patch.map((c) => [c.url, JSON.parse(c.data as string)])).toEqual([
      ['/admin/subjects/s1', { name: '國文', sort_order: 1, is_active: true }],
    ])
    expect(dialogOpen(wrapper)).toBe(false)
  })

  it('SubjectTable requires name', async () => {
    mock.onGet(LIST_URL).reply(200, SUBJECTS)
    const { wrapper } = await mountTable()

    await clickIn(wrapper.find('.ref-toolbar'), '新增科目')
    await formItem('名稱').find('input').setValue('  ')
    await saveDialog()
    await settleErrors()

    expect(formItem('名稱').find('.el-form-item__error').text()).toBe('請輸入名稱')
    expect(mock.history.post).toHaveLength(0)
    expect(dialogOpen(wrapper)).toBe(true)
  })

  it('SubjectTable shows conflict and validation errors under name', async () => {
    mock.onGet(LIST_URL).reply(200, SUBJECTS)
    mock
      .onPost(LIST_URL)
      .replyOnce(409, { error: { code: 'subject_name_taken', message: '名稱已存在', details: null } })
      .onPost(LIST_URL)
      .replyOnce(422, {
        error: {
          code: 'validation_error',
          message: '輸入資料格式錯誤',
          details: [{ loc: ['body', 'name'], msg: '名稱不可空白', type: 'value_error' }],
        },
      })
    const { wrapper } = await mountTable()

    await clickIn(wrapper.find('.ref-toolbar'), '新增科目')
    await formItem('名稱').find('input').setValue('國語')
    await saveDialog()
    await settleErrors()
    expect(formItem('名稱').find('.el-form-item__error').text()).toBe('名稱已存在')
    expect(dialogOpen(wrapper)).toBe(true)

    await formItem('名稱').find('input').setValue('國語 ')
    await saveDialog()
    await settleErrors()
    expect(formItem('名稱').find('.el-form-item__error').text()).toBe('名稱不可空白')
    expect(dialogOpen(wrapper)).toBe(true)
    expect(listRequests()).toBe(1)
  })

  it('SubjectTable delete deactivates referenced subject', async () => {
    mock.onGet(LIST_URL).reply(200, SUBJECTS)
    mock.onDelete('/admin/subjects/s1').reply(200, { deleted: false, deactivated: true })
    const { wrapper, lookups } = await mountTable()

    await clickIn(rowOf(wrapper, '國語'), '刪除')
    expect(lastMessageBox().textContent).toContain('確定要刪除科目「國語」嗎？')
    await clickInMessageBox('刪除')

    expect(mock.history.delete.map((c) => c.url)).toEqual(['/admin/subjects/s1'])
    expect(document.body.textContent).toContain('「國語」已有資料引用，已改為停用')
    expect(listRequests()).toBe(2)
    expect(lookups.invalidate).toHaveBeenCalledWith('subjects')
  })

  it('SubjectTable deletes unreferenced subject', async () => {
    mock.onGet(LIST_URL).reply(200, SUBJECTS)
    mock.onDelete('/admin/subjects/s9').reply(200, { deleted: true, deactivated: false })
    const { wrapper } = await mountTable()

    await clickIn(rowOf(wrapper, '作文'), '刪除')
    await clickInMessageBox('刪除')

    expect(document.body.textContent).toContain('已刪除')
    expect(document.body.textContent).not.toContain('已改為停用')
    expect(listRequests()).toBe(2)
  })

  it('SubjectTable hides write actions without permission', async () => {
    mock.onGet(LIST_URL).reply(200, SUBJECTS)
    const { wrapper } = await mountTable(READER)

    expect(rows(wrapper)).toHaveLength(3)
    expect(buttonsText(wrapper)).not.toContain('新增科目')
    expect(buttonsText(wrapper)).not.toContain('編輯')
    expect(buttonsText(wrapper)).not.toContain('刪除')
    expect(headers(wrapper)).toEqual(['名稱', '排序', '狀態'])
  })

  it('SubjectTable empty state', async () => {
    mock.onGet(LIST_URL).reply(200, [])
    const { wrapper } = await mountTable()

    expect(wrapper.text()).toContain('尚未建立科目')
    expect(buttonsText(wrapper.find('.empty-state'))).toEqual(['新增科目'])
    wrapper.unmount()
    document.body.innerHTML = ''

    const { wrapper: reader } = await mountTable(READER)
    expect(reader.text()).toContain('尚未建立科目')
    expect(buttonsText(reader)).toEqual([])
  })

  it('SubjectTable shows load error with retry', async () => {
    mock
      .onGet(LIST_URL)
      .replyOnce(500, { error: { code: 'internal_error', message: '伺服器錯誤', details: null } })
      .onGet(LIST_URL)
      .reply(200, SUBJECTS)
    const { wrapper } = await mountTable()

    const error = wrapper.find('.empty-state.is-error')
    expect(error.text()).toContain('無法載入科目')
    expect(wrapper.find('.el-table').exists()).toBe(false)

    await clickIn(error, '重試')
    expect(rows(wrapper)).toHaveLength(3)
    expect(wrapper.find('.empty-state').exists()).toBe(false)
  })

  it('SubjectTable shows loading mask while fetching', async () => {
    let release: () => void = () => undefined
    mock.onGet(LIST_URL).reply(
      () =>
        new Promise((resolve) => {
          release = () => resolve([200, SUBJECTS])
        }),
    )
    const { wrapper } = await mountTable()

    expect(wrapper.find('.el-loading-mask').exists()).toBe(true)
    release()
    await settle()
    expect(rows(wrapper)).toHaveLength(3)
  })
})
