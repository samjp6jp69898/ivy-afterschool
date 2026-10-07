import { DOMWrapper, flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { adminHttp } from '@/api/http'
import FormDialog from '@/components/common/FormDialog.vue'
import { useLookupsStore } from '@/stores/lookups'
import { createApiMock, mountWithApp } from '@/test/helpers'
import SchoolTable from './SchoolTable.vue'

const LIST_URL = '/admin/schools'
const SCHOOLS = [
  { id: 'sc1', name: '某某國民小學', short_name: '某某國小', is_active: true },
  { id: 'sc2', name: '光明國民小學', short_name: '光明', is_active: true },
  { id: 'sc4', name: '另一國民小學', short_name: null, is_active: false },
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
  const mounted = await mountWithApp(SchoolTable, {
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

describe('SchoolTable', () => {
  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    document.body.innerHTML = ''
  })

  it('SchoolTable lists schools with short name', async () => {
    mock.onGet(LIST_URL).reply(200, SCHOOLS)
    const { wrapper } = await mountTable()

    expect(rowOf(wrapper, '某某國民小學').text()).toContain('某某國小')
    const names = SCHOOLS.map((s) => s.name).sort((a, b) => a.localeCompare(b, 'zh-Hant'))
    expect(rows(wrapper).map((r) => r.findAll('td')[0]?.text())).toEqual(names)
    expect(wrapper.findAll('.el-table__header th').map((th) => th.text())).toEqual(['名稱', '簡稱', '狀態', '操作'])
    expect(rowOf(wrapper, '另一國民小學').findAll('td')[1]?.text()).toBe('—')
    expect(rowOf(wrapper, '另一國民小學').classes()).toContain('ref-row-inactive')
    expect(wrapper.find('.ref-toolbar__hint').text()).toBe('學生資料的就讀國小下拉；簡稱用於學生列表與家長端')
  })

  it('SchoolTable creates school', async () => {
    mock.onGet(LIST_URL).reply(200, SCHOOLS)
    mock.onPost(LIST_URL).reply(201, { id: 'sc9', name: '大同國民小學', short_name: '大同', is_active: true })
    const { wrapper, lookups } = await mountTable()

    await clickIn(wrapper.find('.ref-toolbar'), '新增合作國小')
    expect(dialog().find('.el-dialog__title').text()).toBe('新增合作國小')
    expect(formItem('名稱').find('input').attributes('maxlength')).toBe('50')
    expect(formItem('簡稱').find('input').attributes('maxlength')).toBe('20')
    expect(formItem('簡稱').text()).toContain('學生列表與家長端顯示用；空白時顯示完整名稱')
    await formItem('名稱').find('input').setValue('大同國民小學')
    await formItem('簡稱').find('input').setValue('大同')
    await clickIn(dialog(), '儲存')

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({
      name: '大同國民小學',
      short_name: '大同',
      is_active: true,
    })
    expect(dialogOpen(wrapper)).toBe(false)
    expect(listRequests()).toBe(2)
    expect(lookups.invalidate).toHaveBeenCalledWith('schools')
  })

  it('SchoolTable edits school and clears short name to null', async () => {
    mock.onGet(LIST_URL).reply(200, SCHOOLS)
    mock.onPatch('/admin/schools/sc2').reply(200, { ...SCHOOLS[1], short_name: null })
    const { wrapper } = await mountTable()

    await clickIn(rowOf(wrapper, '光明國民小學'), '編輯')
    expect(dialog().find('.el-dialog__title').text()).toBe('編輯合作國小')
    expect(formItem('簡稱').find<HTMLInputElement>('input').element.value).toBe('光明')
    await formItem('簡稱').find('input').setValue('  ')
    await clickIn(dialog(), '儲存')

    expect(mock.history.patch.map((c) => [c.url, JSON.parse(c.data as string)])).toEqual([
      ['/admin/schools/sc2', { name: '光明國民小學', short_name: null, is_active: true }],
    ])
  })

  it('SchoolTable shows duplicate name under name field', async () => {
    mock.onGet(LIST_URL).reply(200, SCHOOLS)
    mock.onPost(LIST_URL).reply(409, { error: { code: 'school_name_taken', message: '名稱已存在', details: null } })
    const { wrapper } = await mountTable()

    await clickIn(wrapper.find('.ref-toolbar'), '新增合作國小')
    await formItem('名稱').find('input').setValue('光明國民小學')
    await clickIn(dialog(), '儲存')
    await settleErrors()

    expect(formItem('名稱').find('.el-form-item__error').text()).toBe('名稱已存在')
    expect(dialogOpen(wrapper)).toBe(true)
  })

  it('SchoolTable deletes unreferenced school', async () => {
    mock.onGet(LIST_URL).reply(200, SCHOOLS)
    mock.onDelete('/admin/schools/sc2').reply(200, { deleted: true, deactivated: false })
    mock.onDelete('/admin/schools/sc1').reply(200, { deleted: false, deactivated: true })
    const { wrapper, lookups } = await mountTable()

    await clickIn(rowOf(wrapper, '光明國民小學'), '刪除')
    expect(document.body.querySelector('.el-message-box')?.textContent).toContain('確定要刪除合作國小「光明國民小學」嗎？')
    await clickInMessageBox('刪除')
    expect(document.body.textContent).toContain('已刪除')
    expect(listRequests()).toBe(2)

    await clickIn(rowOf(wrapper, '某某國民小學'), '刪除')
    await clickInMessageBox('刪除')
    expect(document.body.textContent).toContain('「某某國民小學」已有資料引用，已改為停用')
    expect(listRequests()).toBe(3)
    expect(lookups.invalidate).toHaveBeenCalledWith('schools')
  })

  it('SchoolTable hides write actions without permission', async () => {
    mock.onGet(LIST_URL).reply(200, SCHOOLS)
    const { wrapper } = await mountTable(READER)

    expect(rows(wrapper)).toHaveLength(3)
    expect(buttonsText(wrapper)).toEqual([])
    expect(wrapper.findAll('.el-table__header th').map((th) => th.text())).toEqual(['名稱', '簡稱', '狀態'])
  })

  it('SchoolTable empty and load error states', async () => {
    mock.onGet(LIST_URL).reply(200, [])
    const { wrapper } = await mountTable()
    expect(wrapper.text()).toContain('尚未建立合作國小')
    expect(buttonsText(wrapper.find('.empty-state'))).toEqual(['新增合作國小'])
    wrapper.unmount()
    document.body.innerHTML = ''
    mock.reset()

    mock
      .onGet(LIST_URL)
      .replyOnce(500, { error: { code: 'internal_error', message: '伺服器錯誤', details: null } })
      .onGet(LIST_URL)
      .reply(200, SCHOOLS)
    const { wrapper: failed } = await mountTable()
    const error = failed.find('.empty-state.is-error')
    expect(error.text()).toContain('無法載入合作國小')
    await clickIn(error, '重試')
    expect(rows(failed)).toHaveLength(3)
  })
})
