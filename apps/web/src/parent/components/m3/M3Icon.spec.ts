import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import M3Icon from './M3Icon.vue'

describe('M3Icon', () => {
  it('M3Icon renders ligature and size', () => {
    const wrapper = mount(M3Icon, { props: { name: 'home', size: 32 } })
    expect(wrapper.text()).toBe('home')
    expect(wrapper.attributes('style')).toContain('font-size: 32px')
    expect(wrapper.classes()).toEqual(expect.arrayContaining(['material-symbols-rounded', 'm3-icon']))
  })

  it('M3Icon default size and string size', () => {
    expect(mount(M3Icon, { props: { name: 'home' } }).attributes('style')).toContain('font-size: 24px')
    expect(mount(M3Icon, { props: { name: 'home', size: '1.5em' } }).attributes('style')).toContain(
      'font-size: 1.5em',
    )
  })

  it('M3Icon decorative vs labelled', () => {
    const decorative = mount(M3Icon, { props: { name: 'schedule' } })
    expect(decorative.attributes('aria-hidden')).toBe('true')
    expect(decorative.attributes('role')).toBeUndefined()
    expect(decorative.attributes('aria-label')).toBeUndefined()

    const labelled = mount(M3Icon, { props: { name: 'notifications', label: '通知' } })
    expect(labelled.attributes('role')).toBe('img')
    expect(labelled.attributes('aria-label')).toBe('通知')
    expect(labelled.attributes('aria-hidden')).toBeUndefined()
  })

  it('M3Icon filled variation', () => {
    const filled = mount(M3Icon, { props: { name: 'check_circle', filled: true } })
    expect(filled.attributes('style')).toMatch(/font-variation-settings: ["']FILL["'] 1/)
    const outlined = mount(M3Icon, { props: { name: 'check_circle' } })
    expect(outlined.attributes('style')).toMatch(/font-variation-settings: ["']FILL["'] 0/)
  })

  it('M3Icon is not interactive', () => {
    const wrapper = mount(M3Icon, { props: { name: 'help', label: '說明' } })
    expect(wrapper.element.tagName).toBe('SPAN')
    expect(wrapper.attributes('tabindex')).toBeUndefined()
    expect(wrapper.attributes('role')).toBe('img')
  })
})
