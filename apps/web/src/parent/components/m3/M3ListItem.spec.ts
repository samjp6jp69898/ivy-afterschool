import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { mountWithApp } from '../../../test/helpers'
import M3ListItem from './M3ListItem.vue'

function keydown(key: string): KeyboardEvent {
  return new KeyboardEvent('keydown', { key, cancelable: true, bubbles: true })
}

describe('M3ListItem', () => {
  it('M3ListItem renders headline and supporting', () => {
    const wrapper = mount(M3ListItem, { props: { headline: '出勤紀錄', supportingText: '月曆檢視' } })
    expect(wrapper.element.tagName).toBe('LI')
    expect(wrapper.text()).toContain('出勤紀錄')
    expect(wrapper.text()).toContain('月曆檢視')
    expect(wrapper.get('.m3-list-item__headline').text()).toBe('出勤紀錄')
    expect(wrapper.get('.m3-list-item__supporting').text()).toBe('月曆檢視')
    expect(wrapper.classes()).toContain('is-two-line')
  })

  it('M3ListItem one line has no supporting element', () => {
    const wrapper = mount(M3ListItem, { props: { headline: '請假' } })
    expect(wrapper.find('.m3-list-item__supporting').exists()).toBe(false)
    expect(wrapper.classes()).not.toContain('is-two-line')
  })

  it('M3ListItem navigates via to', async () => {
    const { wrapper, router } = await mountWithApp(M3ListItem, { props: { headline: '出勤紀錄', to: '/attendance' } })
    expect(wrapper.element.tagName).toBe('LI')
    const link = wrapper.get('a')
    expect(link.attributes('href')).toBe('/attendance')
    expect(wrapper.find('[role="button"]').exists()).toBe(false)
    await link.trigger('click')
    await new Promise((resolve) => setTimeout(resolve))
    expect(router.currentRoute.value.path).toBe('/attendance')
  })

  it('M3ListItem disabled blocks click', async () => {
    const wrapper = mount(M3ListItem, { props: { headline: '加入官方帳號', clickable: true, disabled: true } })
    const inner = wrapper.get('.m3-list-item__inner')
    await inner.trigger('click')
    inner.element.dispatchEvent(keydown('Enter'))
    inner.element.dispatchEvent(keydown(' '))
    expect(wrapper.emitted('click')).toBeUndefined()
    expect(inner.attributes('aria-disabled')).toBe('true')
    expect(inner.attributes('tabindex')).toBe('-1')
    expect(wrapper.classes()).toContain('is-disabled')
  })

  it('M3ListItem disabled with to does not navigate', async () => {
    const { wrapper, router } = await mountWithApp(M3ListItem, {
      props: { headline: '出勤紀錄', to: '/attendance', disabled: true },
    })
    // 停用的導頁列不是真連結：沒有 a、沒有 href
    expect(wrapper.find('a').exists()).toBe(false)
    const inner = wrapper.get('.m3-list-item__inner')
    expect(inner.attributes('role')).toBe('link')
    expect(inner.attributes('aria-disabled')).toBe('true')
    await inner.trigger('click')
    await new Promise((resolve) => setTimeout(resolve))
    expect(router.currentRoute.value.path).toBe('/')
    expect(wrapper.emitted('click')).toBeUndefined()
  })

  it('M3ListItem clickable is a keyboard-operable button inside li', async () => {
    const wrapper = mount(M3ListItem, { props: { headline: '登出', clickable: true } })
    expect(wrapper.element.tagName).toBe('LI')
    const inner = wrapper.get('.m3-list-item__inner')
    expect(inner.attributes('role')).toBe('button')
    expect(inner.attributes('tabindex')).toBe('0')
    expect(inner.attributes('aria-disabled')).toBeUndefined()

    await inner.trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)

    const enter = keydown('Enter')
    inner.element.dispatchEvent(enter)
    const space = keydown(' ')
    inner.element.dispatchEvent(space)
    expect(wrapper.emitted('click')).toHaveLength(3)
    expect(enter.defaultPrevented).toBe(true)
    expect(space.defaultPrevented).toBe(true)
  })

  it('M3ListItem non interactive row has no role, tabindex or click emit', async () => {
    const wrapper = mount(M3ListItem, { props: { headline: '版本' } })
    const inner = wrapper.get('.m3-list-item__inner')
    expect(inner.attributes('role')).toBeUndefined()
    expect(inner.attributes('tabindex')).toBeUndefined()
    expect(inner.classes()).not.toContain('is-interactive')
    await inner.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
  })

  it('M3ListItem renders leading and trailing icons or slots', () => {
    const icons = mount(M3ListItem, {
      props: { headline: '通知', leadingIcon: 'notifications', trailingIcon: 'chevron_right' },
    })
    expect(icons.get('.m3-list-item__leading').classes()).toContain('is-icon')
    expect(icons.get('.m3-list-item__leading .m3-icon').text()).toBe('notifications')
    expect(icons.get('.m3-list-item__trailing .m3-icon').text()).toBe('chevron_right')

    const slots = mount(M3ListItem, {
      props: { headline: '版本' },
      slots: { leading: '<b class="lead">L</b>', trailing: '<span class="trail">1.0.0</span>' },
    })
    expect(slots.get('.m3-list-item__leading').classes()).not.toContain('is-icon')
    expect(slots.get('.lead').text()).toBe('L')
    expect(slots.get('.trail').text()).toBe('1.0.0')

    const bare = mount(M3ListItem, { props: { headline: '請假' } })
    expect(bare.find('.m3-list-item__leading').exists()).toBe(false)
    expect(bare.find('.m3-list-item__trailing').exists()).toBe(false)
  })
})
