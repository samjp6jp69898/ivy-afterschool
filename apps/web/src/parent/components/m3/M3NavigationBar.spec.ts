import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import M3NavigationBar from './M3NavigationBar.vue'
import source from './M3NavigationBar.vue?raw'

const ITEMS = [
  { key: 'home', label: '首頁', icon: 'home', path: '/' },
  { key: 'homework', label: '作業', icon: 'assignment', path: '/homework' },
  { key: 'pickup', label: '接送', icon: 'directions_walk', path: '/pickup' },
  { key: 'exams', label: '成績', icon: 'grading', path: '/exams' },
  { key: 'more', label: '更多', icon: 'menu', path: '/more' },
]

function withBadge(badge: number | undefined) {
  return ITEMS.map((item) => (item.key === 'more' ? { ...item, badge } : item))
}

describe('M3NavigationBar', () => {
  it('M3NavigationBar marks current item', () => {
    const wrapper = mount(M3NavigationBar, { props: { items: ITEMS, currentKey: 'pickup' } })
    const tabs = wrapper.findAll('button.m3-nav-tab')
    expect(tabs).toHaveLength(5)
    const current = tabs.filter((t) => t.attributes('aria-current') === 'page')
    expect(current).toHaveLength(1)
    expect(current[0]?.text()).toContain('接送')
    expect(current[0]?.classes()).toContain('is-active')
    for (const tab of tabs.filter((t) => !t.text().includes('接送'))) {
      expect(tab.attributes('aria-current')).toBeUndefined()
      expect(tab.classes()).not.toContain('is-active')
    }
  })

  it('M3NavigationBar uses filled icon only for the active item', () => {
    const wrapper = mount(M3NavigationBar, { props: { items: ITEMS, currentKey: 'exams' } })
    const tabs = wrapper.findAll('button.m3-nav-tab')
    for (const tab of tabs) {
      const style = tab.get('.m3-icon').attributes('style') ?? ''
      const active = tab.text().includes('成績')
      expect(style).toContain(`'FILL' ${active ? 1 : 0}`)
    }
    expect(tabs[3]?.get('.m3-icon').text()).toBe('grading')
  })

  it('M3NavigationBar emits select', async () => {
    const wrapper = mount(M3NavigationBar, { props: { items: ITEMS, currentKey: 'home' } })
    const exams = wrapper.findAll('button.m3-nav-tab').find((t) => t.text().includes('成績'))
    await exams?.trigger('click')
    expect(wrapper.emitted('select')).toHaveLength(1)
    expect(wrapper.emitted('select')?.[0]?.[0]).toBe('exams')
    expect(wrapper.emitted('select')?.[0]?.[1]).toEqual(ITEMS[3])
  })

  it('M3NavigationBar emits select even for the current item', async () => {
    const wrapper = mount(M3NavigationBar, { props: { items: ITEMS, currentKey: 'home' } })
    await wrapper.findAll('button.m3-nav-tab')[0]?.trigger('click')
    expect(wrapper.emitted('select')?.[0]?.[0]).toBe('home')
  })

  it('M3NavigationBar badge caps at 99', () => {
    const big = mount(M3NavigationBar, { props: { items: withBadge(120), currentKey: 'home' } })
    expect(big.get('.m3-nav-tab__badge').text()).toBe('99+')

    const zero = mount(M3NavigationBar, { props: { items: withBadge(0), currentKey: 'home' } })
    expect(zero.find('.m3-nav-tab__badge').exists()).toBe(false)

    const three = mount(M3NavigationBar, { props: { items: withBadge(3), currentKey: 'home' } })
    expect(three.get('.m3-nav-tab__badge').text()).toBe('3')

    const edge = mount(M3NavigationBar, { props: { items: withBadge(99), currentKey: 'home' } })
    expect(edge.get('.m3-nav-tab__badge').text()).toBe('99')

    const none = mount(M3NavigationBar, { props: { items: withBadge(undefined), currentKey: 'home' } })
    expect(none.find('.m3-nav-tab__badge').exists()).toBe(false)
  })

  it('M3NavigationBar badge is hidden from AT and read as visually hidden text', () => {
    const wrapper = mount(M3NavigationBar, { props: { items: withBadge(3), currentKey: 'home' } })
    const more = wrapper.findAll('button.m3-nav-tab')[4]
    expect(more?.get('.m3-nav-tab__badge').attributes('aria-hidden')).toBe('true')
    expect(more?.get('.visually-hidden').text()).toBe('，3 則未讀')

    const noBadge = wrapper.findAll('button.m3-nav-tab')[0]
    expect(noBadge?.find('.visually-hidden').exists()).toBe(false)
  })

  it('M3NavigationBar is a labelled nav landmark of 80px plus safe area', () => {
    const wrapper = mount(M3NavigationBar, { props: { items: ITEMS, currentKey: 'home' } })
    expect(wrapper.element.tagName).toBe('NAV')
    expect(wrapper.attributes('aria-label')).toBe('主要功能')
    // happy-dom 不算版面：以原始碼斷言樣式規則
    expect(source).toContain('env(safe-area-inset-bottom')
    expect(source).toMatch(/height:\s*80px/)
    expect(source).toMatch(/:focus-visible/)
  })
})
