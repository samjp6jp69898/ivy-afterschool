import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { PickupPerson } from '../../api/pickupPersons'
import PickupPersonListItem from './PickupPersonListItem.vue'
import source from './PickupPersonListItem.vue?raw'

const PHOTO = 'https://acct.r2.cloudflarestorage.com/afterschool/pickup-person-photos/p.jpg'

function person(overrides: Partial<PickupPerson> = {}): PickupPerson {
  return {
    id: 'p1',
    student_id: 's1',
    name: '李阿姨',
    relation: '保母',
    phone: '0912000123',
    photo_url: PHOTO,
    created_at: '2026-09-20T02:00:00Z',
    ...overrides,
  }
}

function mountItem(p: PickupPerson) {
  return mount(PickupPersonListItem, { props: { person: p } })
}

/** ?raw 原始碼 <style> 中，選擇器清單含指定選擇器（可與其他選擇器群組）的所有樣式區塊內容，依出現順序 */
function rulesOf(selector: string): string[] {
  const css = source.slice(source.indexOf('>', source.indexOf('<style')) + 1).replace(/\/\*[\s\S]*?\*\//g, '')
  return Array.from(css.matchAll(/([^{}]+)\{([^}]*)\}/g))
    .filter((m) => (m[1] ?? '').split(',').some((s) => s.trim() === selector))
    .map((m) => m[2] ?? '')
}

describe('PickupPersonListItem', () => {
  it('PickupPersonListItem renders info and emits delete', async () => {
    const p = person()
    const wrapper = mountItem(p)

    expect(wrapper.text()).toContain('李阿姨')
    expect(wrapper.text()).toContain('保母 · 0912000123')
    expect(wrapper.get('.ppl__name').text()).toBe('李阿姨')
    expect(wrapper.get('.ppl__meta').text()).toBe('保母 · 0912000123')
    expect(wrapper.get('img').attributes('src')).toBe(PHOTO)

    await wrapper.get('button[aria-label="刪除 李阿姨"]').trigger('click')

    expect(wrapper.emitted('delete')).toHaveLength(1)
    expect((wrapper.emitted('delete')![0]![0] as PickupPerson).name).toBe('李阿姨')
    expect(wrapper.emitted('delete')![0]![0]).toEqual(p)
  })

  it('PickupPersonListItem falls back to initial', async () => {
    const noPhoto = mountItem(person({ photo_url: null }))

    expect(noPhoto.find('img').exists()).toBe(false)
    expect(noPhoto.get('.ppl__avatar').text()).toBe('李')

    const broken = mountItem(person())
    expect(broken.find('img').exists()).toBe(true)

    await broken.get('img').trigger('error')

    expect(broken.find('img').exists()).toBe(false)
    expect(broken.get('.ppl__avatar').text()).toBe('李')
  })

  it('PickupPersonListItem is a list row with a decorative avatar', () => {
    const wrapper = mountItem(person())

    expect(wrapper.element.tagName).toBe('LI')
    expect(wrapper.classes()).toContain('ppl')
    expect(wrapper.get('.ppl__avatar').attributes('aria-hidden')).toBe('true')
    const img = wrapper.get('img')
    expect(img.attributes('alt')).toBe('')
    expect(img.attributes('loading')).toBe('lazy')
    // 整列只有刪除鈕可操作：沒有編輯、沒有撥號連結
    expect(wrapper.findAll('button')).toHaveLength(1)
    expect(wrapper.find('a').exists()).toBe(false)
    expect(wrapper.attributes('tabindex')).toBeUndefined()
  })

  it('PickupPersonListItem delete button is a standard icon button', () => {
    const wrapper = mountItem(person({ name: '王爺爺' }))

    const button = wrapper.get('button')
    expect(button.attributes('aria-label')).toBe('刪除 王爺爺')
    expect(button.attributes('type')).toBe('button')
    expect(button.classes()).toContain('m3-icon-button--standard')
    expect(button.get('.m3-icon').text()).toBe('delete')
  })

  it('PickupPersonListItem shows the phone as returned', () => {
    const mobile = mountItem(person({ phone: '0912000123' }))
    const landline = mountItem(person({ relation: '親戚', phone: '02-2345-6789' }))

    expect(mobile.get('.ppl__meta').text()).toBe('保母 · 0912000123')
    expect(landline.get('.ppl__meta').text()).toBe('親戚 · 02-2345-6789')
    expect(landline.find('a').exists()).toBe(false)
  })

  it('PickupPersonListItem retries when the photo url changes', async () => {
    const wrapper = mountItem(person())
    await wrapper.get('img').trigger('error')
    expect(wrapper.find('img').exists()).toBe(false)

    // 重抓清單會拿到新的短效網址：重新嘗試載入
    await wrapper.setProps({ person: person({ photo_url: `${PHOTO}?sig=new` }) })
    expect(wrapper.get('img').attributes('src')).toBe(`${PHOTO}?sig=new`)

    // 同一個已失效的網址不再重試
    await wrapper.setProps({ person: person({ photo_url: PHOTO }) })
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.get('.ppl__avatar').text()).toBe('李')
  })

  it('PickupPersonListItem only the delete button emits', async () => {
    const wrapper = mountItem(person())

    await wrapper.trigger('click')
    await wrapper.get('.ppl__text').trigger('click')
    await wrapper.get('.ppl__avatar').trigger('click')

    expect(wrapper.emitted('delete')).toBeUndefined()
  })

  it('PickupPersonListItem initial keeps a whole character', () => {
    // 罕用字（擴充 B 區）佔兩個 UTF-16 code unit，不能只切一個
    const wrapper = mountItem(person({ name: '𠮷野家', photo_url: null }))

    expect(wrapper.get('.ppl__avatar').text()).toBe('𠮷')
  })

  it('PickupPersonListItem keeps the approved layout rules', () => {
    // 一列：最小高 72px、頭像 / 文字 / 刪除鈕橫排、列間 1px outline-variant 分隔線由列自己畫
    const row = rulesOf('.ppl')[0] ?? ''
    expect(row).toMatch(/display:\s*flex/)
    expect(row).toMatch(/align-items:\s*center/)
    expect(row).toMatch(/gap:\s*16px/)
    expect(row).toMatch(/min-height:\s*72px/)
    expect(row).toMatch(/padding:\s*8px 4px 8px 16px/)
    expect(rulesOf('.ppl + .ppl').join('')).toMatch(/border-top:\s*1px solid var\(--m3-outline-variant\)/)
    // 40px 圓形頭像，無照片時 tertiary-container 底
    const avatar = rulesOf('.ppl__avatar').join('')
    expect(avatar).toMatch(/width:\s*40px/)
    expect(avatar).toMatch(/height:\s*40px/)
    expect(avatar).toMatch(/border-radius:\s*var\(--m3-shape-full\)/)
    expect(avatar).toMatch(/background:\s*var\(--m3-tertiary-container\)/)
    expect(rulesOf('.ppl__avatar img').join('')).toMatch(/object-fit:\s*cover/)
    // 文字欄可收縮，姓名與「關係 · 電話」各自單行省略，刪除鈕不被擠掉
    expect(rulesOf('.ppl__text').join('')).toMatch(/min-width:\s*0/)
    for (const selector of ['.ppl__name', '.ppl__meta']) {
      const rule = rulesOf(selector).join('')
      expect(rule, selector).toMatch(/text-overflow:\s*ellipsis/)
      expect(rule, selector).toMatch(/white-space:\s*nowrap/)
    }
    expect(rulesOf('.ppl__meta').join('')).toMatch(/color:\s*var\(--m3-on-surface-variant\)/)
  })
})
