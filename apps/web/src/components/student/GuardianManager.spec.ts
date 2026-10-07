import { DOMWrapper, flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import type { Guardian } from '@/api/guardians'
import { adminHttp } from '@/api/http'
import { createApiMock, mountWithApp } from '@/test/helpers'
import BindingCodeDialog from './BindingCodeDialog.vue'
import GuardianManager from './GuardianManager.vue'

const LIST_URL = '/admin/students/s1/guardians'

const BOUND: Guardian = {
  id: 'g1',
  student_id: 's1',
  name: '王大明',
  relation: 'father',
  phone: '0912-000-111',
  is_primary: true,
  can_pickup: true,
  receives_notifications: true,
  binding: { status: 'bound', parent_display_name: '大明', code_expires_at: null },
}
const CODE_ISSUED: Guardian = {
  id: 'g2',
  student_id: 's1',
  name: '林美麗',
  relation: 'mother',
  phone: '0912-000-123',
  is_primary: false,
  can_pickup: true,
  receives_notifications: false,
  binding: { status: 'code_issued', parent_display_name: null, code_expires_at: '2026-10-09T08:00:00Z' },
}
const UNBOUND: Guardian = {
  id: 'g3',
  student_id: 's1',
  name: '王阿嬤',
  relation: 'grandparent',
  phone: null,
  is_primary: false,
  can_pickup: false,
  receives_notifications: false,
  binding: { status: 'unbound', parent_display_name: null, code_expires_at: null },
}
const ISSUED = { guardian_id: 'g3', code: 'K7M2Q9XP', expires_at: '2026-10-09T08:00:00Z' }

const WRITER = ['students:read', 'guardians:write']
const READER = ['students:read']

let mock: MockAdapter

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
}

async function mountManager(
  options: { permissions?: string[]; props?: Record<string, unknown> } = {},
): Promise<VueWrapper> {
  const { wrapper } = await mountWithApp(GuardianManager, {
    props: { studentId: 's1', studentName: '王小明', ...options.props },
    piniaInitialState: {
      auth: {
        status: 'authenticated',
        user: {
          id: 'u1',
          username: 'clerk01',
          display_name: '林行政',
          role: { id: 'r-clerk', code: 'clerk', name: '行政' },
          permissions: options.permissions ?? WRITER,
          must_change_password: false,
        },
      },
    },
  })
  await settle()
  return wrapper as VueWrapper
}

function cards(wrapper: VueWrapper): DOMWrapper<Element>[] {
  return wrapper.findAll('.guardian-card')
}

function card(wrapper: VueWrapper, name: string): DOMWrapper<Element> {
  const found = cards(wrapper).find((c) => c.find('.guardian-card__name').text() === name)
  if (!found) throw new Error(`找不到監護人 ${name}`)
  return found
}

function buttonsText(root: DOMWrapper<Element> | VueWrapper): string[] {
  return root.findAll('button').map((b) => b.text())
}

async function clickIn(root: DOMWrapper<Element> | VueWrapper, text: string): Promise<void> {
  const button = root.findAll('button').find((b) => b.text() === text)
  if (!button) throw new Error(`找不到按鈕「${text}」`)
  await button.trigger('click')
  await settle()
}

function lastMessageBox(): HTMLElement {
  const box = Array.from(document.body.querySelectorAll<HTMLElement>('.el-message-box')).at(-1)
  if (!box) throw new Error('message box 沒有出現')
  return box
}

async function clickInMessageBox(text: string): Promise<void> {
  const button = Array.from(lastMessageBox().querySelectorAll<HTMLButtonElement>('button')).find(
    (b) => b.textContent?.trim() === text,
  )
  if (!button) throw new Error(`message box 找不到按鈕「${text}」`)
  button.click()
  await settle()
}

function dialogByTitle(title: string): DOMWrapper<Element> {
  const el = Array.from(document.body.querySelectorAll('.el-dialog')).find(
    (d) => d.querySelector('.el-dialog__title')?.textContent?.trim() === title,
  )
  if (!el) throw new Error(`找不到 dialog「${title}」`)
  return new DOMWrapper(el)
}

function formItemIn(dialog: DOMWrapper<Element>, label: string): DOMWrapper<Element> {
  const item = dialog
    .findAll('.el-form-item')
    .find((i) => i.find('.el-form-item__label').text().trim() === label)
  if (!item) throw new Error(`找不到欄位 ${label}`)
  return item
}

async function chooseOption(item: DOMWrapper<Element>, label: string): Promise<void> {
  await item.find('.el-select__wrapper').trigger('click')
  await settle()
  const listId = item.find('input').attributes('aria-controls')
  const option = Array.from(
    document.getElementById(listId!)?.querySelectorAll<HTMLElement>('.el-select-dropdown__item') ?? [],
  ).find((o) => o.textContent?.trim() === label)
  if (!option) throw new Error(`找不到選項 ${label}`)
  option.click()
  await settle()
}

function listRequests(url = LIST_URL): number {
  return mock.history.get.filter((c) => c.url === url).length
}

function deferredList(list: Guardian[] | null): { release: () => void } {
  let release: () => void = () => undefined
  mock.onGet(LIST_URL).replyOnce(
    () =>
      new Promise((resolve) => {
        release = () =>
          resolve(
            list === null
              ? [500, { error: { code: 'internal_error', message: '伺服器錯誤', details: null } }]
              : [200, list],
          )
      }),
  )
  return { release: () => release() }
}

describe('GuardianManager', () => {
  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    document.body.innerHTML = ''
  })

  it('GuardianManager renders binding states', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND, CODE_ISSUED, UNBOUND])
    const wrapper = await mountManager()

    expect(card(wrapper, '王大明').find('.guardian-card__binding').text()).toBe('已綁定（大明）')
    expect(card(wrapper, '林美麗').find('.guardian-card__binding').text()).toBe('綁定碼有效至 2026/10/09 16:00')
    expect(card(wrapper, '王阿嬤').find('.guardian-card__binding').text()).toBe('未綁定')
    expect(card(wrapper, '王大明').find('.guardian-card__binding .el-tag').classes()).toContain('el-tag--success')
    expect(card(wrapper, '林美麗').find('.guardian-card__binding .el-tag').classes()).toContain('el-tag--warning')
    expect(card(wrapper, '王阿嬤').find('.guardian-card__binding .el-tag').classes()).toContain('el-tag--info')

    expect(card(wrapper, '王大明').find('.guardian-card__primary').text()).toBe('主要')
    expect(card(wrapper, '林美麗').text()).not.toContain('主要')
    expect(card(wrapper, '王阿嬤').text()).not.toContain('主要')
  })

  it('GuardianManager shows contact details and primary summary', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND, CODE_ISSUED, UNBOUND])
    const wrapper = await mountManager()

    expect(wrapper.find('.guardian-toolbar').text()).toContain('主要聯絡人：王大明')
    const father = card(wrapper, '王大明')
    expect(father.find('.guardian-card__relation').text()).toBe('父親')
    expect(father.find('.guardian-card__phone').text()).toBe('0912-000-111')
    expect(card(wrapper, '王阿嬤').find('.guardian-card__relation').text()).toBe('祖父母')
    expect(card(wrapper, '王阿嬤').find('.guardian-card__phone').text()).toBe('—')

    const flags = card(wrapper, '林美麗').findAll('.guardian-card__flag')
    expect(flags.map((f) => f.text())).toEqual(['可接送 ✓', '收通知 —'])
    expect(flags.map((f) => f.classes().includes('is-off'))).toEqual([false, true])
    document.body.innerHTML = ''

    mock.onGet(LIST_URL).reply(200, [CODE_ISSUED, UNBOUND])
    const noPrimary = await mountManager()
    expect(noPrimary.find('.guardian-toolbar').text()).toContain('尚未設定主要聯絡人')
  })

  it('GuardianManager issues binding code and shows dialog', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND, CODE_ISSUED, UNBOUND])
    mock.onPost('/admin/guardians/g3/binding-code').reply(200, ISSUED)
    const wrapper = await mountManager()

    await clickIn(card(wrapper, '王阿嬤'), '產生綁定碼')

    expect(mock.history.post.map((c) => c.url)).toEqual(['/admin/guardians/g3/binding-code'])
    const dialog = dialogByTitle('家長綁定碼')
    expect(dialog.find('[data-test=binding-code]').text()).toBe('K7M2-Q9XP')
    expect(dialog.text()).toContain('請 王阿嬤 在 LINE 開啟家長端，輸入此綁定碼即可綁定 王小明。')
    expect(document.body.textContent).toContain('K7M2-Q9XP')
    expect(listRequests()).toBe(2)
    expect(wrapper.emitted('change')).toHaveLength(1)
  })

  it('GuardianManager confirms before regenerating code', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND, CODE_ISSUED, UNBOUND])
    const wrapper = await mountManager()
    expect(buttonsText(card(wrapper, '林美麗'))).toContain('重新產生綁定碼')
    expect(buttonsText(card(wrapper, '林美麗'))).not.toContain('產生綁定碼')

    await clickIn(card(wrapper, '林美麗'), '重新產生綁定碼')
    expect(lastMessageBox().textContent).toContain('重新產生後舊的綁定碼會立即失效，確定嗎？')
    await clickInMessageBox('取消')

    expect(mock.history.post.length).toBe(0)
    expect(wrapper.emitted('change')).toBeUndefined()
  })

  it('GuardianManager regenerates code after confirm', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND, CODE_ISSUED, UNBOUND])
    mock.onPost('/admin/guardians/g2/binding-code').reply(200, { ...ISSUED, guardian_id: 'g2', code: 'P4X8N2RT' })
    const wrapper = await mountManager()

    await clickIn(card(wrapper, '林美麗'), '重新產生綁定碼')
    await clickInMessageBox('重新產生')

    expect(mock.history.post.map((c) => c.url)).toEqual(['/admin/guardians/g2/binding-code'])
    expect(dialogByTitle('家長綁定碼').find('[data-test=binding-code]').text()).toBe('P4X8-N2RT')
    expect(wrapper.emitted('change')).toHaveLength(1)
  })

  it('GuardianManager removes binding code once dialog closes', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND, CODE_ISSUED, UNBOUND])
    mock.onPost('/admin/guardians/g3/binding-code').reply(200, ISSUED)
    const wrapper = await mountManager()
    await clickIn(card(wrapper, '王阿嬤'), '產生綁定碼')
    expect(document.body.textContent).toContain('K7M2-Q9XP')

    await clickIn(dialogByTitle('家長綁定碼'), '完成')
    await clickInMessageBox('確定關閉')

    expect(document.body.textContent).not.toContain('K7M2-Q9XP')
    expect(document.body.textContent).not.toContain('K7M2Q9XP')
    expect(wrapper.findComponent(BindingCodeDialog).props('code')).toBe('')
  })

  it('GuardianManager unbinds bound guardian', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND, CODE_ISSUED, UNBOUND])
    mock.onPost('/admin/guardians/g1/unbind').reply(200, {
      ...BOUND,
      binding: { status: 'unbound', parent_display_name: null, code_expires_at: null },
    })
    const wrapper = await mountManager()

    await clickIn(card(wrapper, '王大明'), '解除綁定')
    expect(lastMessageBox().textContent).toContain('解除後 大明 將無法在家長端看到 王小明，確定要解除嗎？')
    await clickInMessageBox('解除綁定')

    expect(mock.history.post.map((c) => c.url)).toEqual(['/admin/guardians/g1/unbind'])
    expect(wrapper.emitted('change')).toHaveLength(1)
    expect(listRequests()).toBe(2)
    expect(document.body.textContent).toContain('已解除綁定')
  })

  it('GuardianManager does not unbind when cancelled', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND])
    const wrapper = await mountManager()

    await clickIn(card(wrapper, '王大明'), '解除綁定')
    await clickInMessageBox('取消')

    expect(mock.history.post.length).toBe(0)
    expect(wrapper.emitted('change')).toBeUndefined()
  })

  it('GuardianManager hides actions for readers and archived student', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND, CODE_ISSUED, UNBOUND])
    const writer = await mountManager()
    expect(buttonsText(writer)).toEqual(
      expect.arrayContaining(['新增監護人', '產生綁定碼', '重新產生綁定碼', '解除綁定', '編輯', '刪除']),
    )
    writer.unmount()
    document.body.innerHTML = ''

    const reader = await mountManager({ permissions: READER })
    expect(cards(reader)).toHaveLength(3)
    expect(buttonsText(reader)).toEqual([])
    expect(reader.find('.guardian-card__actions').exists()).toBe(false)
    reader.unmount()
    document.body.innerHTML = ''

    const archived = await mountManager({ props: { readonly: true } })
    expect(cards(archived)).toHaveLength(3)
    expect(buttonsText(archived)).toEqual([])
  })

  it('GuardianManager empty state', async () => {
    mock.onGet(LIST_URL).reply(200, [])
    const wrapper = await mountManager()

    expect(wrapper.text()).toContain('尚未建立監護人')
    expect(wrapper.find('.empty-state').findAll('button').map((b) => b.text())).toEqual(['新增監護人'])
    expect(wrapper.find('.guardian-toolbar').text()).not.toContain('主要聯絡人')
    document.body.innerHTML = ''

    const reader = await mountManager({ permissions: READER })
    expect(reader.text()).toContain('尚未建立監護人')
    expect(buttonsText(reader)).toEqual([])
  })

  it('GuardianManager deletes guardian with bound warning', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND, UNBOUND])
    mock.onDelete('/admin/guardians/g1').reply(204)
    const wrapper = await mountManager()

    await clickIn(card(wrapper, '王阿嬤'), '刪除')
    expect(lastMessageBox().textContent).toContain('確定要刪除 王阿嬤 嗎？')
    expect(lastMessageBox().textContent).not.toContain('已綁定家長帳號')
    await clickInMessageBox('取消')

    await clickIn(card(wrapper, '王大明'), '刪除')
    expect(lastMessageBox().textContent).toContain(
      '確定要刪除 王大明 嗎？此監護人已綁定家長帳號，刪除後家長將無法再看到此學生。',
    )
    await clickInMessageBox('刪除')

    expect(mock.history.delete.map((c) => c.url)).toEqual(['/admin/guardians/g1'])
    expect(wrapper.emitted('change')).toHaveLength(1)
    expect(listRequests()).toBe(2)
  })

  it('GuardianManager adds guardian through form dialog', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND])
    mock.onPost(LIST_URL).reply(201, { ...UNBOUND, id: 'g9', name: '王小美', relation: 'other' })
    const wrapper = await mountManager()

    await clickIn(wrapper.find('.guardian-toolbar'), '新增監護人')
    const dialog = dialogByTitle('新增監護人')
    await formItemIn(dialog, '主要聯絡人').find('.el-switch').trigger('click')
    await settle()
    expect(dialog.text()).toContain('將取代原本的主要聯絡人')

    await formItemIn(dialog, '姓名').find('input').setValue('王小美')
    await chooseOption(formItemIn(dialog, '關係'), '其他')
    await clickIn(dialog, '儲存')

    expect(JSON.parse(mock.history.post[0]?.data as string)).toMatchObject({ name: '王小美', relation: 'other', is_primary: true })
    expect(wrapper.emitted('change')).toHaveLength(1)
    expect(listRequests()).toBe(2)
  })

  it('GuardianManager edits guardian with its own primary flag', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND, CODE_ISSUED])
    mock.onPatch('/admin/guardians/g1').reply(200, { ...BOUND, phone: '0912-000-222' })
    const wrapper = await mountManager()

    await clickIn(card(wrapper, '王大明'), '編輯')
    const dialog = dialogByTitle('編輯監護人')
    expect(formItemIn(dialog, '姓名').find<HTMLInputElement>('input').element.value).toBe('王大明')
    // 編輯的就是主要聯絡人本人：不提示取代
    expect(dialog.text()).not.toContain('將取代原本的主要聯絡人')

    await formItemIn(dialog, '電話').find('input').setValue('0912-000-222')
    await clickIn(dialog, '儲存')

    expect(mock.history.patch.map((c) => [c.url, JSON.parse(c.data as string)])).toEqual([
      ['/admin/guardians/g1', { phone: '0912-000-222' }],
    ])
    expect(wrapper.emitted('change')).toHaveLength(1)
    expect(listRequests()).toBe(2)
  })

  it('GuardianManager shows backend conflict message and reloads', async () => {
    mock.onGet(LIST_URL).reply(200, [UNBOUND])
    mock.onPost('/admin/guardians/g3/binding-code').reply(409, {
      error: { code: 'guardian_already_bound', message: '此監護人已綁定家長帳號，請先解除綁定', details: null },
    })
    const wrapper = await mountManager()

    await clickIn(card(wrapper, '王阿嬤'), '產生綁定碼')

    expect(document.body.textContent).toContain('此監護人已綁定家長帳號，請先解除綁定')
    expect(document.body.querySelector('[data-test=binding-code]')).toBeNull()
    expect(wrapper.emitted('change')).toBeUndefined()
    expect(listRequests()).toBe(2)
  })

  it('GuardianManager shows unbind conflict message', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND])
    mock.onPost('/admin/guardians/g1/unbind').reply(409, {
      error: { code: 'guardian_not_bound', message: '此監護人尚未綁定家長帳號', details: null },
    })
    const wrapper = await mountManager()

    await clickIn(card(wrapper, '王大明'), '解除綁定')
    await clickInMessageBox('解除綁定')

    expect(document.body.textContent).toContain('此監護人尚未綁定家長帳號')
    expect(wrapper.emitted('change')).toBeUndefined()
    expect(listRequests()).toBe(2)
  })

  it('GuardianManager shows initial guardians before loading latest', async () => {
    const { release } = deferredList([BOUND, UNBOUND])
    const wrapper = await mountManager({ props: { initial: [UNBOUND] } })

    expect(cards(wrapper).map((c) => c.find('.guardian-card__name').text())).toEqual(['王阿嬤'])
    expect(wrapper.find('.el-skeleton').exists()).toBe(false)

    release()
    await settle()
    expect(cards(wrapper).map((c) => c.find('.guardian-card__name').text())).toEqual(['王大明', '王阿嬤'])
  })

  it('GuardianManager shows loading then error with retry', async () => {
    const { release } = deferredList(null)
    const wrapper = await mountManager()
    expect(wrapper.find('.el-skeleton').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('尚未建立監護人')

    release()
    await settle()
    expect(wrapper.find('.el-skeleton').exists()).toBe(false)
    expect(wrapper.find('.empty-state.is-error').text()).toContain('無法載入監護人')
    expect(wrapper.text()).not.toContain('尚未建立監護人')

    mock.onGet(LIST_URL).reply(200, [BOUND])
    await clickIn(wrapper.find('.empty-state'), '重試')
    expect(cards(wrapper)).toHaveLength(1)
    expect(wrapper.find('.empty-state').exists()).toBe(false)
  })

  it('GuardianManager reloads when student changes', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND])
    mock.onGet('/admin/students/s2/guardians').reply(200, [{ ...CODE_ISSUED, student_id: 's2' }])
    const wrapper = await mountManager()

    await wrapper.setProps({ studentId: 's2', studentName: '陳小美' })
    await settle()

    expect(listRequests('/admin/students/s2/guardians')).toBe(1)
    expect(cards(wrapper).map((c) => c.find('.guardian-card__name').text())).toEqual(['林美麗'])
  })

  it('GuardianManager disables actions while a request is pending', async () => {
    mock.onGet(LIST_URL).reply(200, [BOUND, UNBOUND])
    let release: () => void = () => undefined
    mock.onPost('/admin/guardians/g3/binding-code').reply(
      () =>
        new Promise((resolve) => {
          release = () => resolve([200, ISSUED])
        }),
    )
    const wrapper = await mountManager()

    await clickIn(card(wrapper, '王阿嬤'), '產生綁定碼')
    const disabled = (): boolean[] =>
      wrapper.findAll('.guardian-card__actions button').map((b) => b.attributes('disabled') !== undefined)
    // 兩張卡各三個動作鈕（編輯、產生綁定碼 / 解除綁定、刪除）
    expect(disabled()).toEqual([true, true, true, true, true, true])

    release()
    await settle()
    expect(disabled()).toEqual([false, false, false, false, false, false])
  })
})
