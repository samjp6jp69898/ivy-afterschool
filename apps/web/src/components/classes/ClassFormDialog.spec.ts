import { DOMWrapper, flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import type { ClassItem } from '@/api/classes'
import { adminHttp } from '@/api/http'
import { useLookupsStore } from '@/stores/lookups'
import { createApiMock, mountWithApp } from '@/test/helpers'
import ClassFormDialog from './ClassFormDialog.vue'

const CLASS_A: ClassItem = {
  id: 'c1',
  name: '低年級A班',
  grade_levels: [1, 2],
  academic_year: 115,
  sort_order: 0,
  archived_at: null,
  student_count: 18,
  staff: [],
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

async function mountDialog(props: Record<string, unknown> = {}) {
  const mounted = await mountWithApp(ClassFormDialog, {
    props: { modelValue: true, defaultAcademicYear: 115, ...props },
  })
  await settle()
  return { wrapper: mounted.wrapper as VueWrapper, lookups: useLookupsStore(mounted.pinia) }
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

async function checkGrade(label: string): Promise<void> {
  const box = formItem('年級')
    .findAll('.el-checkbox-button')
    .find((b) => b.text() === label)
  if (!box) throw new Error(`找不到年級 ${label}`)
  await box.find('input').setValue(true)
  await settle()
}

function submitButton(): DOMWrapper<Element> {
  const btn = dialog()
    .findAll('.el-dialog__footer button')
    .find((b) => b.text() === '儲存')
  if (!btn) throw new Error('找不到「儲存」')
  return btn
}

async function submit(): Promise<void> {
  await submitButton().trigger('click')
  await settle()
}

describe('ClassFormDialog', () => {
  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    document.body.innerHTML = ''
  })

  it('ClassFormDialog creates class', async () => {
    const created = { ...CLASS_A, id: 'c9', name: '中年級A班', grade_levels: [3, 4] }
    mock.onPost('/admin/classes').reply(201, created)
    const { wrapper, lookups } = await mountDialog()

    expect(dialog().find('.el-dialog__title').text()).toBe('新增班級')
    expect(formItem('學年度').find('.el-select').text()).toContain('115 學年度')
    expect(formItem('年級').text()).toContain('可複選（混齡班）')
    await formItem('班級名稱').find('input').setValue('中年級A班')
    await checkGrade('四年級')
    await checkGrade('三年級')
    await submit()

    expect(JSON.parse(mock.history.post[0]?.data as string)).toEqual({
      name: '中年級A班',
      grade_levels: [3, 4],
      academic_year: 115,
      sort_order: 0,
    })
    expect(wrapper.emitted('saved')?.[0]).toEqual([created])
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([false])
    expect(lookups.invalidate).toHaveBeenCalledWith('classes')
  })

  it('ClassFormDialog requires at least one grade', async () => {
    const { wrapper } = await mountDialog()

    await formItem('班級名稱').find('input').setValue('中年級A班')
    await submit()
    await settleErrors()

    expect(dialog().text()).toContain('請至少選擇一個年級')
    expect(dialog().text()).not.toContain('可複選（混齡班）')
    expect(mock.history.post).toHaveLength(0)
    expect(wrapper.emitted('saved')).toBeUndefined()
  })

  it('ClassFormDialog requires name', async () => {
    await mountDialog()

    await formItem('班級名稱').find('input').setValue('   ')
    await checkGrade('一年級')
    await submit()
    await settleErrors()

    expect(formItem('班級名稱').text()).toContain('請輸入班級名稱')
    expect(mock.history.post).toHaveLength(0)
  })

  it('ClassFormDialog maps duplicate name', async () => {
    mock.onPost('/admin/classes').reply(409, {
      error: { code: 'class_name_taken', message: '班級名稱重複', details: null },
    })
    const { wrapper } = await mountDialog()
    await formItem('班級名稱').find('input').setValue('低年級A班')
    await checkGrade('一年級')
    await submit()
    await settleErrors()

    expect(formItem('班級名稱').find('.el-form-item__error').text()).toBe('此學年度已有同名班級')
    expect(wrapper.emitted('saved')).toBeUndefined()
    wrapper.unmount()
    document.body.innerHTML = ''

    mock.onPatch('/admin/classes/c1').reply(200, { ...CLASS_A, sort_order: 2 })
    const { wrapper: edit } = await mountDialog({ klass: CLASS_A, defaultAcademicYear: 116 })
    expect(dialog().find('.el-dialog__title').text()).toBe('編輯班級')
    expect(formItem('學年度').find('.el-select').text()).toContain('115 學年度')
    expect(submitButton().attributes('disabled')).toBeDefined()

    const sortInput = formItem('排序').find('input')
    await sortInput.setValue('2')
    await sortInput.trigger('change')
    await settle()
    expect(submitButton().attributes('disabled')).toBeUndefined()
    await submit()

    expect(JSON.parse(mock.history.patch[0]?.data as string)).toEqual({ sort_order: 2 })
    expect(edit.emitted('saved')?.[0]).toEqual([{ ...CLASS_A, sort_order: 2 }])
  })

  it('ClassFormDialog shows archived conflict message', async () => {
    mock.onPatch('/admin/classes/c1').reply(409, {
      error: { code: 'class_archived', message: '班級已封存，無法修改', details: null },
    })
    const { wrapper } = await mountDialog({ klass: CLASS_A })

    await formItem('班級名稱').find('input').setValue('低年級甲班')
    await submit()

    expect(JSON.parse(mock.history.patch[0]?.data as string)).toEqual({ name: '低年級甲班' })
    expect(document.body.textContent).toContain('班級已封存，無法修改')
    expect(wrapper.emitted('saved')).toBeUndefined()
  })
})
