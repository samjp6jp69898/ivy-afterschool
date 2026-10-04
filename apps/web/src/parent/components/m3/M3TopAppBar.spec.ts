import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import M3TopAppBar from './M3TopAppBar.vue'
import source from './M3TopAppBar.vue?raw'

function setScrollY(value: number): void {
  Object.defineProperty(window, 'scrollY', { value, configurable: true })
  window.dispatchEvent(new Event('scroll'))
}

afterEach(() => {
  setScrollY(0)
})

describe('M3TopAppBar', () => {
  it('M3TopAppBar shows back and emits', async () => {
    const wrapper = mount(M3TopAppBar, { props: { title: '請假', showBack: true } })
    expect(wrapper.text()).toContain('請假')
    expect(wrapper.get('h1').text()).toBe('請假')
    await wrapper.get('button[aria-label="返回"]').trigger('click')
    expect(wrapper.emitted('back')).toHaveLength(1)
  })

  it('M3TopAppBar leading slot without back', () => {
    const wrapper = mount(M3TopAppBar, {
      props: { title: '首頁', showBack: false },
      slots: { leading: '<img alt="logo">' },
    })
    expect(wrapper.find('img[alt="logo"]').exists()).toBe(true)
    expect(wrapper.find('button[aria-label="返回"]').exists()).toBe(false)
  })

  it('M3TopAppBar back button replaces leading slot when showBack', () => {
    const wrapper = mount(M3TopAppBar, {
      props: { title: '成績', showBack: true },
      slots: { leading: '<img alt="logo">' },
    })
    expect(wrapper.find('button[aria-label="返回"]').exists()).toBe(true)
    expect(wrapper.find('img[alt="logo"]').exists()).toBe(false)
  })

  it('M3TopAppBar renders actions slot on the right of the title', () => {
    const wrapper = mount(M3TopAppBar, {
      props: { title: '通知' },
      slots: { actions: '<button aria-label="全部已讀">x</button>' },
    })
    const actions = wrapper.get('.m3-top-app-bar__actions')
    expect(actions.find('button[aria-label="全部已讀"]').exists()).toBe(true)
    expect(wrapper.find('.m3-top-app-bar__title').exists()).toBe(true)
  })

  it('M3TopAppBar adds is-scrolled when scrolled prop or window scrolled', async () => {
    const wrapper = mount(M3TopAppBar, { props: { title: '成績' } })
    expect(wrapper.classes()).not.toContain('is-scrolled')
    setScrollY(24)
    await wrapper.vm.$nextTick()
    expect(wrapper.classes()).toContain('is-scrolled')
    setScrollY(0)
    await wrapper.vm.$nextTick()
    expect(wrapper.classes()).not.toContain('is-scrolled')
    await wrapper.setProps({ scrolled: true })
    expect(wrapper.classes()).toContain('is-scrolled')
  })

  it('M3TopAppBar removes the same scroll listener it added on unmount', () => {
    const added: unknown[] = []
    const removed: unknown[] = []
    const add = vi.spyOn(window, 'addEventListener').mockImplementation((type, fn) => {
      if (type === 'scroll') added.push(fn)
    })
    const remove = vi.spyOn(window, 'removeEventListener').mockImplementation((type, fn) => {
      if (type === 'scroll') removed.push(fn)
    })
    const wrapper = mount(M3TopAppBar, { props: { title: '成績' } })
    wrapper.unmount()
    add.mockRestore()
    remove.mockRestore()
    expect(added).toHaveLength(1)
    expect(removed).toEqual(added)
  })

  it('M3TopAppBar style follows design decisions', () => {
    expect(source).toMatch(/position:\s*sticky/)
    expect(source).toMatch(/top:\s*0/)
    expect(source).toMatch(/min-height:\s*64px/)
    expect(source).toMatch(/grid-template-columns:\s*48px minmax\(0,\s*1fr\) auto/)
    expect(source).toMatch(/\.m3-top-app-bar\.is-scrolled\s*\{[^}]*var\(--m3-surface-container\)/)
    expect(source).toMatch(/text-overflow:\s*ellipsis/)
    expect(source).toMatch(/white-space:\s*nowrap/)
    expect(source).not.toMatch(/box-shadow/)
  })
})
