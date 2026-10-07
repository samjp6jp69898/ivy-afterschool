import { DOMWrapper, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ChildSummary } from '../api/children'
import ChildContextHeader from './ChildContextHeader.vue'
import source from './ChildContextHeader.vue?raw'

const PHOTO_A = 'https://acct.r2.cloudflarestorage.com/afterschool/student-photos/s1.jpg'
const PHOTO_B = 'https://acct.r2.cloudflarestorage.com/afterschool/student-photos/s2.jpg'

function child(id: string, name: string, overrides: Partial<ChildSummary> = {}): ChildSummary {
  return {
    id,
    name,
    grade_level: 3,
    class_name: '中年級 A 班',
    school_name: '某某國小',
    photo_url: null,
    status: 'active',
    ...overrides,
  }
}

const MING = child('s1', '王小明')
const HUA = child('s2', '王小華', { grade_level: 5, class_name: '高年級 B 班' })
const MEI = child('s3', '王小美', { grade_level: 1, class_name: '低年級班', school_name: '某某實驗國民小學' })

let mounted: VueWrapper[] = []

function mountHeader(children: ChildSummary[], selectedId: string | null, attrs: Record<string, unknown> = {}) {
  const wrapper = mount(ChildContextHeader, { attachTo: document.body, props: { children, selectedId }, attrs })
  mounted.push(wrapper)
  return wrapper
}

function dialog(): HTMLElement | null {
  return document.body.querySelector('[role="dialog"]')
}

function options(): DOMWrapper<Element>[] {
  return Array.from(document.body.querySelectorAll('.cch-option')).map((el) => new DOMWrapper(el))
}

function optionOf(name: string): DOMWrapper<Element> {
  const found = options().find((o) => o.get('.cch-option__name').text() === name)
  if (!found) throw new Error(`找不到選項 ${name}`)
  return found
}

/** ?raw 原始碼中某選擇器（行首，可縮排）的所有樣式區塊內容，依出現順序 */
function rulesOf(selector: string): string[] {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return Array.from(source.matchAll(new RegExp(`^\\s*${escaped}\\s*\\{([^}]*)\\}`, 'gm'))).map((m) => m[1] ?? '')
}

afterEach(() => {
  for (const w of mounted) w.unmount()
  mounted = []
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

describe('ChildContextHeader', () => {
  it('ChildContextHeader shows selected child info', () => {
    const wrapper = mountHeader([MING], 's1')

    const card = wrapper.get('.cch')
    expect(card.text()).toContain('王小明')
    expect(card.text()).toContain('3 年級 · 中年級 A 班')
    expect(card.text()).toContain('某某國小')
    expect(wrapper.get('.cch__name').text()).toBe('王小明')
    expect(wrapper.get('.cch__sub').text()).toBe('3 年級 · 中年級 A 班')
    expect(wrapper.get('.cch__school').text()).toBe('某某國小')
    expect(wrapper.get('.cch__avatar').text()).toBe('王')
    expect(card.findAll('.m3-icon').map((i) => i.text())).not.toContain('expand_more')
  })

  it('ChildContextHeader switches via sheet', async () => {
    const wrapper = mountHeader([MING, HUA], 's1')
    expect(dialog()).toBeNull()

    await wrapper.get('.cch').trigger('click')

    const sheet = dialog()
    expect(sheet).not.toBeNull()
    expect(sheet?.textContent).toContain('選擇小孩')
    await optionOf('王小華').trigger('click')

    expect(wrapper.emitted('select')).toEqual([['s2']])
    expect(dialog()).toBeNull()
  })

  it('ChildContextHeader omits null fields', () => {
    const wrapper = mountHeader([child('s1', '王小明', { class_name: null, school_name: null })], 's1')

    const card = wrapper.get('.cch')
    expect(card.text()).toContain('3 年級')
    expect(card.text()).not.toContain('null')
    expect(card.text()).not.toContain('·')
    expect(wrapper.get('.cch__sub').text()).toBe('3 年級')
    expect(wrapper.find('.cch__school').exists()).toBe(false)
  })

  it('ChildContextHeader renders nothing without children', () => {
    const wrapper = mountHeader([], null)

    expect(wrapper.html()).toBe('<!--v-if-->')
    expect(document.body.querySelector('.cch')).toBeNull()
    expect(dialog()).toBeNull()
  })

  it('ChildContextHeader photo falls back to initial', async () => {
    const wrapper = mountHeader([child('s1', '王小明', { photo_url: PHOTO_A })], 's1')

    const img = wrapper.get('.cch__avatar img')
    expect(img.attributes('src')).toBe(PHOTO_A)
    expect(img.attributes('alt')).toBe('')
    expect(wrapper.get('.cch__avatar').attributes('aria-hidden')).toBe('true')

    await img.trigger('error')

    expect(wrapper.find('.cch__avatar img').exists()).toBe(false)
    expect(wrapper.get('.cch__avatar').text()).toBe('王')
  })

  it('ChildContextHeader marks suspended child', async () => {
    const suspended = child('s1', '王小明', { status: 'suspended' })
    const wrapper = mountHeader([suspended, HUA], 's1')

    expect(wrapper.get('.cch').text()).toContain('暫停')
    expect(wrapper.get('.cch .status-pill').classes()).toContain('status-pill--warning')

    await wrapper.get('.cch').trigger('click')
    const marked = options().filter((o) => o.text().includes('暫停'))
    expect(marked).toHaveLength(1)
    expect(marked[0]!.get('.cch-option__name').text()).toBe('王小明')
    expect(optionOf('王小華').text()).not.toContain('暫停')

    await wrapper.setProps({ selectedId: 's2' })
    expect(wrapper.get('.cch').text()).not.toContain('暫停')
    expect(wrapper.find('.cch .status-pill').exists()).toBe(false)
  })

  it('ChildContextHeader single child is not interactive', async () => {
    const wrapper = mountHeader([MING], 's1')

    const card = wrapper.get('.cch')
    expect(card.element.tagName).toBe('DIV')
    expect(card.attributes('aria-haspopup')).toBeUndefined()
    expect(card.attributes('type')).toBeUndefined()
    expect(card.classes()).not.toContain('is-interactive')
    expect(wrapper.find('.cch__chevron').exists()).toBe(false)
    expect(wrapper.find('.visually-hidden').exists()).toBe(false)

    await card.trigger('click')
    expect(dialog()).toBeNull()
  })

  it('ChildContextHeader multiple children makes the card a dialog button', () => {
    const wrapper = mountHeader([MING, HUA], 's1')

    const card = wrapper.get('.cch')
    expect(card.element.tagName).toBe('BUTTON')
    expect(card.attributes('type')).toBe('button')
    expect(card.attributes('aria-haspopup')).toBe('dialog')
    expect(card.classes()).toContain('is-interactive')
    expect(card.get('.cch__chevron').text()).toBe('expand_more')
    expect(card.get('.cch__chevron').attributes('aria-hidden')).toBe('true')
    expect(card.get('.visually-hidden').text()).toBe('，切換小孩')
  })

  it('ChildContextHeader falls back to the first child when selectedId is unknown', async () => {
    const unknown = mountHeader([MING, HUA], 's9')
    const none = mountHeader([MING, HUA], null)

    expect(unknown.get('.cch__name').text()).toBe('王小明')
    expect(none.get('.cch__name').text()).toBe('王小明')

    await unknown.get('.cch').trigger('click')
    expect(optionOf('王小明').attributes('aria-current')).toBe('true')
    expect(optionOf('王小華').attributes('aria-current')).toBeUndefined()
  })

  it('ChildContextHeader sheet lists every child and marks the current one', async () => {
    const wrapper = mountHeader([MING, HUA, MEI], 's2')

    await wrapper.get('.cch').trigger('click')

    expect(options().map((o) => o.get('.cch-option__name').text())).toEqual(['王小明', '王小華', '王小美'])
    expect(optionOf('王小明').get('.cch-option__sub').text()).toBe('3 年級 · 中年級 A 班')
    expect(optionOf('王小美').get('.cch-option__sub').text()).toBe('1 年級 · 低年級班')
    // 清單內不放國小名稱，避免列太擠
    expect(options().map((o) => o.text()).join('')).not.toContain('某某')
    const current = optionOf('王小華')
    expect(current.attributes('aria-current')).toBe('true')
    expect(current.classes()).toContain('is-selected')
    expect(current.get('.cch-option__check').text()).toBe('check')
    for (const other of [optionOf('王小明'), optionOf('王小美')]) {
      expect(other.attributes('aria-current')).toBeUndefined()
      expect(other.classes()).not.toContain('is-selected')
      expect(other.find('.cch-option__check').exists()).toBe(false)
    }
  })

  it('ChildContextHeader picking the current child only closes the sheet', async () => {
    const wrapper = mountHeader([MING, HUA], 's1')
    await wrapper.get('.cch').trigger('click')

    await optionOf('王小明').trigger('click')

    expect(wrapper.emitted('select')).toBeUndefined()
    expect(dialog()).toBeNull()
  })

  it('ChildContextHeader closes the sheet when it no longer has several children', async () => {
    const wrapper = mountHeader([MING, HUA], 's1')
    await wrapper.get('.cch').trigger('click')
    expect(dialog()).not.toBeNull()

    // 解除綁定後只剩一位：sheet 隨之關閉，之後清單變多也不會自己重新打開
    await wrapper.setProps({ children: [MING] })
    expect(dialog()).toBeNull()
    await wrapper.setProps({ children: [MING, HUA] })
    expect(dialog()).toBeNull()
    expect(wrapper.get('.cch').element.tagName).toBe('BUTTON')
  })

  it('ChildContextHeader card follows selectedId', async () => {
    const wrapper = mountHeader([MING, HUA], 's1')

    await wrapper.setProps({ selectedId: 's2' })

    expect(wrapper.get('.cch__name').text()).toBe('王小華')
    expect(wrapper.get('.cch__sub').text()).toBe('5 年級 · 高年級 B 班')
  })

  it('ChildContextHeader photo failures are tracked per url', async () => {
    const withPhotos = [child('s1', '王小明', { photo_url: PHOTO_A }), child('s2', '王小華', { photo_url: PHOTO_B })]
    const wrapper = mountHeader(withPhotos, 's1')

    await wrapper.get('.cch__avatar img').trigger('error')
    expect(wrapper.find('.cch__avatar img').exists()).toBe(false)

    // 同一位小孩在清單裡的頭像一併退回首字；另一位小孩不受影響
    await wrapper.get('.cch').trigger('click')
    expect(optionOf('王小明').find('img').exists()).toBe(false)
    expect(optionOf('王小明').get('.cch-option__avatar').text()).toBe('王')
    expect(optionOf('王小華').get('img').attributes('src')).toBe(PHOTO_B)

    // 重抓清單拿到新的短效網址時重新嘗試載入；同一個失效網址不會再重試
    await wrapper.setProps({ children: [child('s1', '王小明', { photo_url: `${PHOTO_A}?sig=new` }), withPhotos[1]!] })
    expect(wrapper.get('.cch__avatar img').attributes('src')).toBe(`${PHOTO_A}?sig=new`)
    await wrapper.setProps({ children: withPhotos })
    expect(wrapper.find('.cch__avatar img').exists()).toBe(false)
  })

  it('ChildContextHeader initial keeps a whole character', () => {
    // 罕用字（擴充 B 區）佔兩個 UTF-16 code unit，不能只切一個
    const wrapper = mountHeader([child('s1', '𠮷野家')], 's1')

    expect(wrapper.get('.cch__avatar').text()).toBe('𠮷')
  })

  it('ChildContextHeader passes attrs to the card', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const wrapper = mountHeader([MING, HUA], 's1', { class: 'page-context', 'data-testid': 'ctx' })

    const card = wrapper.get('.cch')
    expect(card.classes()).toContain('page-context')
    expect(card.attributes('data-testid')).toBe('ctx')
    expect(warn).not.toHaveBeenCalled()
  })

  it('ChildContextHeader keeps the approved layout rules', () => {
    // 身分卡：surface-container-low 底、圓角 16px、最小高 80px、左右 padding 16px
    const card = rulesOf('.cch')[0] ?? ''
    expect(card).toMatch(/min-height:\s*80px/)
    expect(card).toMatch(/padding:\s*12px 16px/)
    expect(card).toMatch(/border-radius:\s*var\(--m3-shape-large\)/)
    expect(card).toMatch(/background:\s*var\(--m3-surface-container-low\)/)
    // 頭像 48px 圓形（清單內 40px）
    expect(rulesOf('.cch__avatar').join('')).toMatch(/width:\s*48px[^}]*height:\s*48px|height:\s*48px[^}]*width:\s*48px/)
    expect(rulesOf('.cch-option__avatar').join('')).toMatch(/width:\s*40px/)
    // 每行單行省略
    for (const selector of ['.cch__name', '.cch__sub', '.cch__school']) {
      const rule = rulesOf(selector).join('')
      expect(rule, selector).toMatch(/text-overflow:\s*ellipsis/)
      expect(rule, selector).toMatch(/white-space:\s*nowrap/)
    }
    // 清單列 64px；選中者 secondary-container 底
    expect(rulesOf('.cch-option')[0]).toMatch(/min-height:\s*64px/)
    expect(rulesOf('.cch-option.is-selected')[0]).toMatch(/background:\s*var\(--m3-secondary-container\)/)
    // 可點時有 2px focus 外框
    expect(rulesOf('.cch.is-interactive:focus-visible').join('')).toMatch(/outline:\s*2px solid var\(--m3-primary\)/)
  })
})
