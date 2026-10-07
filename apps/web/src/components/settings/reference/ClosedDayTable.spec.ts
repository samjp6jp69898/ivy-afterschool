import { DOMWrapper, flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { adminHttp } from '@/api/http'
import FormDialog from '@/components/common/FormDialog.vue'
import { useLookupsStore } from '@/stores/lookups'
import { createApiMock, mountWithApp } from '@/test/helpers'
import ClosedDayTable from './ClosedDayTable.vue'

const LIST_URL = '/admin/closed-days'
// 後端依日期由新到舊回傳（BACKEND-122）
const DAYS = [
  { id: 'd2', date: '2026-12-25', reason: '行憲紀念日' },
  { id: 'd1', date: '2026-10-10', reason: '國慶日' },
  { id: 'd5', date: '2026-10-05', reason: null },
]
const PAST = { id: 'd0', date: '2026-09-25', reason: '中秋節' }
const WRITER = ['settings:read', 'settings:write']
const READER = ['settings:read']
// 台北 2026-10-02 10:00
const NOW = new Date('2026-10-02T02:00:00Z')

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
  const mounted = await mountWithApp(ClosedDayTable, {
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

function rowWith(wrapper: VueWrapper, text: string): DOMWrapper<Element> {
  const row = rows(wrapper).find((r) => r.text().includes(text))
  if (!row) throw new Error(`找不到含「${text}」的列`)
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

/** 在日期欄輸入顯示格式（YYYY/MM/DD）後觸發 change，由 el-date-picker 解析成 value-format */
async function typeDate(value: string): Promise<void> {
  const input = formItem('日期').find('input')
  await input.trigger('focus')
  await input.setValue(value)
  await input.trigger('change')
  await settle()
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

function listCalls() {
  return mock.history.get.filter((c) => c.url === LIST_URL)
}

describe('ClosedDayTable', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('ClosedDayTable loads upcoming range by default', async () => {
    mock.onGet(LIST_URL).reply(200, [{ id: 'd1', date: '2026-10-10', reason: '國慶日' }])
    const { wrapper } = await mountTable()

    expect(listCalls().map((c) => c.params)).toEqual([{ date_from: '2026-10-02', date_to: '2027-10-02' }])
    const row = rows(wrapper)[0]!
    expect(row.text()).toContain('10/10（六）')
    expect(row.text()).toContain('國慶日')
    expect(row.find('.ref-date__year').text()).toBe('2026')
    expect(wrapper.findAll('.el-table__header th').map((th) => th.text())).toEqual(['日期', '原因', '操作'])
    expect(wrapper.find('.ref-toolbar__hint').text()).toBe('顯示今天起 12 個月內・休息日當天不會建立出勤名單')
  })

  it('ClosedDayTable lists dates near to far with blank reason', async () => {
    mock.onGet(LIST_URL).reply(200, DAYS)
    const { wrapper } = await mountTable()

    expect(rows(wrapper).map((r) => r.find('.ref-date').text())).toEqual([
      '202610/05（一）',
      '202610/10（六）',
      '202612/25（五）',
    ])
    expect(rowWith(wrapper, '10/05').findAll('td')[1]?.text()).toBe('—')
  })

  it('ClosedDayTable creates closed day', async () => {
    mock.onGet(LIST_URL).reply(200, [{ id: 'd1', date: '2026-10-10', reason: '國慶日' }])
    mock.onPost(LIST_URL).reply(201, { id: 'd2', date: '2026-12-25', reason: '行憲紀念日' })
    const { wrapper, lookups } = await mountTable()

    await clickIn(wrapper.find('.ref-toolbar'), '新增休息日')
    expect(dialog().find('.el-dialog__title').text()).toBe('新增休息日')
    expect(dialog().find('.el-alert').text()).toContain('設定為休息日後，當天不會建立出勤名單')
    expect(formItem('原因').find('input').attributes('maxlength')).toBe('100')
    await typeDate('2026/12/25')
    await formItem('原因').find('input').setValue('行憲紀念日')
    await clickIn(dialog(), '儲存')

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({ date: '2026-12-25', reason: '行憲紀念日' })
    expect(dialogOpen(wrapper)).toBe(false)
    expect(listCalls()).toHaveLength(2)
    expect(lookups.invalidate).not.toHaveBeenCalled()
  })

  it('ClosedDayTable sends null for blank reason', async () => {
    mock.onGet(LIST_URL).reply(200, [])
    mock.onPost(LIST_URL).reply(201, { id: 'd9', date: '2026-12-31', reason: null })
    const { wrapper } = await mountTable()

    await clickIn(wrapper.find('.ref-toolbar'), '新增休息日')
    await typeDate('2026/12/31')
    await formItem('原因').find('input').setValue('  ')
    await clickIn(dialog(), '儲存')

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({ date: '2026-12-31', reason: null })
  })

  it('ClosedDayTable requires date', async () => {
    mock.onGet(LIST_URL).reply(200, DAYS)
    const { wrapper } = await mountTable()

    await clickIn(wrapper.find('.ref-toolbar'), '新增休息日')
    await formItem('原因').find('input').setValue('員工旅遊')
    await clickIn(dialog(), '儲存')
    await settleErrors()

    expect(formItem('日期').find('.el-form-item__error').text()).toBe('請選擇日期')
    expect(mock.history.post).toHaveLength(0)
  })

  it('ClosedDayTable edit keeps date readonly', async () => {
    mock.onGet(LIST_URL).reply(200, DAYS)
    mock.onPatch('/admin/closed-days/d1').reply(200, { id: 'd1', date: '2026-10-10', reason: '颱風停班' })
    const { wrapper } = await mountTable()

    await clickIn(rowWith(wrapper, '國慶日'), '編輯')
    expect(dialog().find('.el-dialog__title').text()).toBe('編輯休息日')
    expect(formItem('日期').find<HTMLInputElement>('input').element.disabled).toBe(true)
    expect(formItem('日期').text()).toContain('日期不可修改；要改日期請刪除後重新新增')
    expect(dialog().find('.el-alert').exists()).toBe(false)
    await formItem('原因').find('input').setValue('颱風停班')
    await clickIn(dialog(), '儲存')

    expect(mock.history.patch.map((c) => [c.url, JSON.parse(c.data as string)])).toEqual([
      ['/admin/closed-days/d1', { reason: '颱風停班' }],
    ])
  })

  it('ClosedDayTable shows duplicate date conflict', async () => {
    mock.onGet(LIST_URL).reply(200, DAYS)
    mock
      .onPost(LIST_URL)
      .reply(409, { error: { code: 'closed_day_exists', message: '該日期已設定為休息日', details: null } })
    const { wrapper } = await mountTable()

    await clickIn(wrapper.find('.ref-toolbar'), '新增休息日')
    await typeDate('2026/10/10')
    await clickIn(dialog(), '儲存')
    await settleErrors()

    expect(formItem('日期').find('.el-form-item__error').text()).toBe('該日期已設定為休息日')
    expect(dialogOpen(wrapper)).toBe(true)
  })

  it('ClosedDayTable toggles past dates', async () => {
    mock.onGet(LIST_URL).reply((config) => {
      const params = config.params as { date_from: string }
      return [200, params.date_from < '2026-10-02' ? [...DAYS, PAST] : DAYS]
    })
    const { wrapper } = await mountTable()

    await wrapper.find('.ref-toolbar input[type=checkbox]').setValue(true)
    await settle()

    expect(listCalls().at(-1)?.params).toEqual({ date_from: '2025-10-02', date_to: '2027-10-02' })
    expect(wrapper.find('.ref-toolbar__hint').text()).toBe('顯示 2025/10/02 ～ 2027/10/02・休息日當天不會建立出勤名單')
    const past = rowWith(wrapper, '中秋節')
    expect(past.classes()).toContain('ref-row-past')
    expect(past.find('.el-tag').text()).toBe('已過')
    expect(rowWith(wrapper, '國慶日').find('.el-tag').exists()).toBe(false)
  })

  it('ClosedDayTable deletes closed day', async () => {
    mock.onGet(LIST_URL).reply(200, DAYS)
    mock.onDelete('/admin/closed-days/d1').reply(200, { deleted: true, deactivated: false })
    const { wrapper } = await mountTable()

    await clickIn(rowWith(wrapper, '國慶日'), '刪除')
    expect(document.body.querySelector('.el-message-box')?.textContent).toContain('確定要刪除休息日「10/10（六） 國慶日」嗎？')
    await clickInMessageBox('刪除')

    expect(mock.history.delete.map((c) => c.url)).toEqual(['/admin/closed-days/d1'])
    expect(document.body.textContent).toContain('已刪除')
    expect(listCalls()).toHaveLength(2)
  })

  it('ClosedDayTable hides write actions without permission', async () => {
    mock.onGet(LIST_URL).reply(200, DAYS)
    const { wrapper } = await mountTable(READER)

    expect(rows(wrapper)).toHaveLength(3)
    expect(buttonsText(wrapper)).toEqual([])
    expect(wrapper.findAll('.el-table__header th').map((th) => th.text())).toEqual(['日期', '原因'])
  })

  it('ClosedDayTable empty and load error states', async () => {
    mock.onGet(LIST_URL).reply(200, [])
    const { wrapper } = await mountTable()
    expect(wrapper.text()).toContain('尚未建立休息日')
    expect(wrapper.text()).toContain('今天起 12 個月內沒有設定休息日')
    expect(buttonsText(wrapper.find('.empty-state'))).toEqual(['新增休息日'])
    await wrapper.find('.ref-toolbar input[type=checkbox]').setValue(true)
    await settle()
    expect(wrapper.text()).toContain('過去一年到未來 12 個月內都沒有設定休息日')
    wrapper.unmount()
    document.body.innerHTML = ''
    mock.reset()

    mock
      .onGet(LIST_URL)
      .replyOnce(500, { error: { code: 'internal_error', message: '伺服器錯誤', details: null } })
      .onGet(LIST_URL)
      .reply(200, DAYS)
    const { wrapper: failed } = await mountTable()
    const error = failed.find('.empty-state.is-error')
    expect(error.text()).toContain('無法載入休息日')
    await clickIn(error, '重試')
    expect(rows(failed)).toHaveLength(3)
  })
})
