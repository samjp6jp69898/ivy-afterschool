import { DOMWrapper, flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import type { Guardian } from '@/api/guardians'
import { adminHttp } from '@/api/http'
import { createApiMock, mountWithApp } from '@/test/helpers'
import GuardianFormDialog from './GuardianFormDialog.vue'

const FATHER: Guardian = {
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
  const { wrapper } = await mountWithApp(GuardianFormDialog, {
    props: { modelValue: true, studentId: 's1', hasOtherPrimary: false, ...props },
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

async function chooseRelation(label: string): Promise<void> {
  const item = formItem('關係')
  await item.find('.el-select__wrapper').trigger('click')
  await settle()
  const listId = item.find('input').attributes('aria-controls')
  const options = Array.from(document.getElementById(listId!)?.querySelectorAll<HTMLElement>('.el-select-dropdown__item') ?? [])
  const option = options.find((o) => o.textContent?.trim() === label)
  if (!option) throw new Error(`找不到關係選項 ${label}`)
  option.click()
  await settle()
}

async function submit(): Promise<void> {
  const btn = dialog()
    .findAll('.el-dialog__footer button')
    .find((b) => b.text() === '儲存')
  if (!btn) throw new Error('找不到「儲存」')
  await btn.trigger('click')
  await settle()
}

describe('GuardianFormDialog', () => {
  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    document.body.innerHTML = ''
  })

  it('GuardianFormDialog creates with defaults', async () => {
    const created = { ...FATHER, id: 'g2', name: '林美麗', relation: 'mother', phone: '0912-000-123', is_primary: false }
    mock.onPost('/admin/students/s1/guardians').reply(201, created)
    const wrapper = await mountDialog()

    expect(dialog().find('.el-dialog__title').text()).toBe('新增監護人')
    await formItem('姓名').find('input').setValue('林美麗')
    await chooseRelation('母親')
    await formItem('電話').find('input').setValue('0912-000-123')
    await submit()

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({
      name: '林美麗',
      relation: 'mother',
      phone: '0912-000-123',
      is_primary: false,
      can_pickup: true,
      receives_notifications: true,
    })
    expect(wrapper.emitted('saved')?.[0]).toEqual([created])
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([false])
  })

  it('GuardianFormDialog sends null phone when left empty', async () => {
    mock.onPost('/admin/students/s1/guardians').reply(201, FATHER)
    await mountDialog()

    await formItem('姓名').find('input').setValue('  王大明 ')
    await chooseRelation('父親')
    await submit()

    expect(JSON.parse(mock.history.post[0]?.data as string)).toMatchObject({ name: '王大明', phone: null })
  })

  it('GuardianFormDialog requires name and relation', async () => {
    const wrapper = await mountDialog()

    await submit()
    await settleErrors()

    expect(formItem('姓名').text()).toContain('請輸入姓名')
    expect(formItem('關係').text()).toContain('請選擇關係')
    expect(mock.history.post).toHaveLength(0)
    expect(wrapper.emitted('saved')).toBeUndefined()
  })

  it('GuardianFormDialog warns when replacing primary', async () => {
    await mountDialog({ hasOtherPrimary: true })
    expect(dialog().text()).not.toContain('將取代原本的主要聯絡人')

    await formItem('主要聯絡人').find('.el-switch').trigger('click')
    await settle()

    expect(dialog().text()).toContain('將取代原本的主要聯絡人')
  })

  it('GuardianFormDialog does not warn without another primary', async () => {
    await mountDialog({ hasOtherPrimary: false })

    await formItem('主要聯絡人').find('.el-switch').trigger('click')
    await settle()

    expect(dialog().text()).not.toContain('將取代原本的主要聯絡人')
  })

  it('GuardianFormDialog validates phone and edits changed fields', async () => {
    const wrapper = await mountDialog()
    await formItem('姓名').find('input').setValue('林美麗')
    await chooseRelation('母親')
    await formItem('電話').find('input').setValue('abc')
    await submit()
    await settleErrors()

    expect(formItem('電話').text()).toContain('電話格式不正確')
    expect(mock.history.post).toHaveLength(0)
    wrapper.unmount()
    document.body.innerHTML = ''

    mock.onPatch('/admin/guardians/g1').reply(200, { ...FATHER, can_pickup: false })
    const edit = await mountDialog({ guardian: FATHER })
    expect(dialog().find('.el-dialog__title').text()).toBe('編輯監護人')
    expect((formItem('姓名').find('input').element as HTMLInputElement).value).toBe('王大明')

    await formItem('可接送').find('.el-switch').trigger('click')
    await submit()

    expect(JSON.parse(mock.history.patch[0]?.data as string)).toEqual({ can_pickup: false })
    expect(edit.emitted('saved')?.[0]).toEqual([{ ...FATHER, can_pickup: false }])
  })

  it('GuardianFormDialog maps validation errors and archived conflict', async () => {
    mock.onPost('/admin/students/s1/guardians').replyOnce(422, {
      error: {
        code: 'validation_error',
        message: '輸入資料有誤',
        details: [{ loc: ['body', 'phone'], msg: '電話長度過長', type: 'string_too_long' }],
      },
    })
    const wrapper = await mountDialog()
    await formItem('姓名').find('input').setValue('林美麗')
    await chooseRelation('母親')
    await submit()
    await settleErrors()

    expect(formItem('電話').text()).toContain('電話長度過長')

    mock.onPost('/admin/students/s1/guardians').replyOnce(409, {
      error: { code: 'student_archived', message: '學生已封存，無法修改監護人', details: null },
    })
    await submit()
    await settle()

    expect(document.body.textContent).toContain('學生已封存，無法修改監護人')
    expect(wrapper.emitted('saved')).toBeUndefined()
  })
})
