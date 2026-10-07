import { mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PickupPersonForm from './PickupPersonForm.vue'

const RELATIONS = ['父親', '母親', '祖父', '祖母', '外公', '外婆', '親戚', '保母', '其他']
const MB = 1024 * 1024

let wrapper: VueWrapper | null = null
let blobCounter = 0
const revoke = vi.fn()

beforeEach(() => {
  blobCounter = 0
  revoke.mockClear()
  URL.createObjectURL = vi.fn(() => `blob:stub-${++blobCounter}`)
  URL.revokeObjectURL = revoke
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

function mountForm(props: { submitting?: boolean } = {}): VueWrapper {
  wrapper = mount(PickupPersonForm, { props, attachTo: document.body })
  return wrapper
}

// get() 找不到會直接 throw，test-utils 的回傳型別不含 exists()
function field(w: VueWrapper, label: string): Omit<DOMWrapper<HTMLInputElement>, 'exists'> {
  const lab = w.findAll('label').find((l) => l.text() === label)
  if (!lab) throw new Error(`找不到欄位 ${label}`)
  return w.get<HTMLInputElement>(`#${lab.attributes('for')}`)
}

function radio(w: VueWrapper, label: string) {
  const found = w.findAll('[role="radio"]').find((r) => r.text().includes(label))
  if (!found) throw new Error(`找不到關係 ${label}`)
  return found
}

function button(w: VueWrapper, label: string) {
  const found = w.findAll('button').find((b) => b.text().includes(label))
  if (!found) throw new Error(`找不到按鈕 ${label}`)
  return found
}

async function fillRequired(w: VueWrapper, phone = '0912-000-123'): Promise<void> {
  await field(w, '姓名').setValue(' 李阿姨 ')
  await radio(w, '保母').trigger('click')
  await field(w, '電話').setValue(phone)
}

function makeFile(sizeBytes: number, name = 'photo.jpg', type = 'image/jpeg'): File {
  return new File([new Uint8Array(sizeBytes)], name, { type })
}

async function pickPhoto(w: VueWrapper, file: File): Promise<void> {
  const input = w.get<HTMLInputElement>('input[type="file"]')
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
}

describe('PickupPersonForm', () => {
  it('PickupPersonForm emits normalized payload', async () => {
    const w = mountForm()
    await fillRequired(w)
    await button(w, '儲存').trigger('click')
    expect(w.emitted('submit')?.[0]?.[0]).toEqual({ name: '李阿姨', relation: '保母', phone: '0912000123', photo: null })
  })

  it('PickupPersonForm rejects bad phone', async () => {
    const w = mountForm()
    await fillRequired(w, '12345')
    await button(w, '儲存').trigger('click')
    expect(w.text()).toContain('請輸入正確的電話號碼')
    expect(w.emitted('submit')).toBeUndefined()
    await field(w, '電話').setValue('0912-000-12')
    expect(w.text()).not.toContain('請輸入正確的電話號碼')
  })

  it('PickupPersonForm accepts landline numbers with separators', async () => {
    const w = mountForm()
    await fillRequired(w, '02 2345-6789')
    await button(w, '儲存').trigger('click')
    expect((w.emitted('submit')?.[0]?.[0] as { phone: string }).phone).toBe('0223456789')
  })

  it('PickupPersonForm rejects oversized photo', async () => {
    const w = mountForm()
    await fillRequired(w)
    await pickPhoto(w, makeFile(6 * MB))
    expect(w.get('[role="alert"]').text()).toContain('照片不可超過 5 MB')
    await button(w, '儲存').trigger('click')
    expect((w.emitted('submit')?.[0]?.[0] as { photo: File | null }).photo).toBeNull()
    expect(w.find('img').exists()).toBe(false)
  })

  it('PickupPersonForm keeps the previous photo when a new one is oversized', async () => {
    const w = mountForm()
    await fillRequired(w)
    const first = makeFile(1024, 'a.jpg')
    await pickPhoto(w, first)
    await pickPhoto(w, makeFile(6 * MB, 'big.jpg'))
    expect(w.get('[role="alert"]').text()).toContain('照片不可超過 5 MB')
    await button(w, '儲存').trigger('click')
    expect((w.emitted('submit')?.[0]?.[0] as { photo: File | null }).photo).toBe(first)
  })

  it('PickupPersonForm photo preview and revoke', async () => {
    const w = mountForm()
    await pickPhoto(w, makeFile(1024))
    expect(w.get('img').attributes('src')).toBe('blob:stub-1')
    await button(w, '移除照片').trigger('click')
    expect(revoke).toHaveBeenCalledWith('blob:stub-1')
    expect(w.find('img').exists()).toBe(false)
  })

  it('PickupPersonForm clears the file input so the same file can be chosen again', async () => {
    const w = mountForm()
    const input = w.get<HTMLInputElement>('input[type="file"]')
    const written: string[] = []
    Object.defineProperty(input.element, 'value', { get: () => '', set: (v: string) => written.push(v), configurable: true })
    await pickPhoto(w, makeFile(1024))
    expect(written).toEqual([''])
  })

  it('PickupPersonForm revokes the old preview on replace and on unmount', async () => {
    const w = mountForm()
    await pickPhoto(w, makeFile(1024, 'a.jpg'))
    await pickPhoto(w, makeFile(2048, 'b.jpg'))
    expect(revoke).toHaveBeenCalledWith('blob:stub-1')
    expect(w.get('img').attributes('src')).toBe('blob:stub-2')
    w.unmount()
    wrapper = null
    expect(revoke).toHaveBeenCalledWith('blob:stub-2')
  })

  it('PickupPersonForm shows a placeholder when the preview cannot be decoded but still submits the photo', async () => {
    const w = mountForm()
    await fillRequired(w)
    const heic = makeFile(2048, 'IMG_1.heic', 'image/heic')
    await pickPhoto(w, heic)
    await w.get('img').trigger('error')
    expect(w.find('img').exists()).toBe(false)
    expect(w.text()).toContain('無法預覽')
    await button(w, '儲存').trigger('click')
    expect((w.emitted('submit')?.[0]?.[0] as { photo: File | null }).photo).toBe(heic)
  })

  it('PickupPersonForm file input accepts image types without forcing capture', () => {
    const input = mountForm().get('input[type="file"]')
    expect(input.attributes('accept')).toBe('image/jpeg,image/png,image/webp,image/heic')
    expect(input.attributes('capture')).toBeUndefined()
    expect(mountForm().text()).toContain('照片可協助老師核對接送人，非必填')
  })

  it('PickupPersonForm disabled while submitting', async () => {
    const w = mountForm()
    await fillRequired(w)
    await w.setProps({ submitting: true })
    const save = button(w, '儲存')
    expect(save.attributes('aria-disabled')).toBe('true')
    expect(save.attributes('aria-busy')).toBe('true')
    await save.trigger('click')
    expect(w.emitted('submit')).toBeUndefined()
    expect(button(w, '取消').attributes('disabled')).toBeDefined()
    expect(field(w, '姓名').attributes('disabled')).toBeDefined()
    expect(field(w, '電話').attributes('disabled')).toBeDefined()
    expect(radio(w, '母親').attributes('disabled')).toBeDefined()
  })

  it('PickupPersonForm relation is single-select chips', async () => {
    const w = mountForm()
    const group = w.get('[role="radiogroup"]')
    const labelledBy = group.attributes('aria-labelledby') ?? ''
    expect(w.get(`[id="${labelledBy}"]`).text()).toBe('關係')
    const radios = group.findAll('[role="radio"]')
    expect(radios.map((r) => r.text())).toEqual(RELATIONS)
    expect(radios.every((r) => r.attributes('aria-pressed') === undefined)).toBe(true)
    expect(button(w, '儲存').attributes('disabled')).toBeDefined()
    await field(w, '姓名').setValue('李阿姨')
    await field(w, '電話').setValue('0912000123')
    expect(button(w, '儲存').attributes('disabled')).toBeDefined()
    await radio(w, '保母').trigger('click')
    await radio(w, '外婆').trigger('click')
    const checked = group.findAll('[role="radio"]').filter((r) => r.attributes('aria-checked') === 'true')
    // 已選 chip 的 leading 圖示 check 也是文字節點
    expect(checked.map((r) => r.text().replace('check', ''))).toEqual(['外婆'])
    expect(group.findAll('[role="radio"]').filter((r) => r.attributes('aria-checked') === 'false')).toHaveLength(8)
    expect(button(w, '儲存').attributes('disabled')).toBeUndefined()
  })

  it('PickupPersonForm keeps save disabled for whitespace-only name and emits cancel', async () => {
    const w = mountForm()
    await field(w, '姓名').setValue('   ')
    await radio(w, '母親').trigger('click')
    await field(w, '電話').setValue('0912000123')
    expect(button(w, '儲存').attributes('disabled')).toBeDefined()
    await button(w, '取消').trigger('click')
    expect(w.emitted('cancel')).toHaveLength(1)
  })

  it('PickupPersonForm name is limited to 50 characters and marks optional photo only', () => {
    const w = mountForm()
    expect(field(w, '姓名').attributes('maxlength')).toBe('50')
    expect(field(w, '姓名').attributes('autocomplete')).toBe('off')
    expect(field(w, '電話').attributes('type')).toBe('tel')
    expect(w.text()).toContain('照片（選填）')
    expect(w.text()).not.toContain('*')
  })
})
