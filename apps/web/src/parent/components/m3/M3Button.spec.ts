import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import M3Button from './M3Button.vue'

describe('M3Button', () => {
  it('M3Button emits click when enabled', async () => {
    const wrapper = mount(M3Button, { slots: { default: '我要來接' } })
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
    expect(wrapper.emitted('click')?.[0]?.[0]).toBeInstanceOf(MouseEvent)
    expect(wrapper.text()).toContain('我要來接')
  })

  it('M3Button blocks click when disabled or loading', async () => {
    const disabled = mount(M3Button, { props: { disabled: true }, slots: { default: '送出' } })
    await disabled.trigger('click')
    expect(disabled.emitted('click')).toBeUndefined()
    expect(disabled.attributes('disabled')).toBeDefined()

    const loading = mount(M3Button, { props: { loading: true }, slots: { default: '送出' } })
    await loading.trigger('click')
    expect(loading.emitted('click')).toBeUndefined()
    expect(loading.attributes('aria-busy')).toBe('true')
  })

  it('M3Button loading keeps focus via aria-disabled instead of native disabled', () => {
    const loading = mount(M3Button, { props: { loading: true }, slots: { default: '送出' } })
    expect(loading.attributes('aria-disabled')).toBe('true')
    expect(loading.attributes('disabled')).toBeUndefined()
    expect(loading.classes()).toContain('is-loading')

    const idle = mount(M3Button, { slots: { default: '送出' } })
    expect(idle.attributes('aria-busy')).toBeUndefined()
    expect(idle.attributes('aria-disabled')).toBeUndefined()
    expect(idle.attributes('disabled')).toBeUndefined()
  })

  it('M3Button applies variant and type', () => {
    const wrapper = mount(M3Button, { props: { variant: 'outlined', type: 'submit' }, slots: { default: '送出' } })
    expect(wrapper.classes()).toContain('m3-button--outlined')
    expect(wrapper.attributes('type')).toBe('submit')

    const defaults = mount(M3Button, { slots: { default: '確認' } })
    expect(defaults.classes()).toContain('m3-button--filled')
    expect(defaults.attributes('type')).toBe('button')
  })

  it('M3Button block adds full width class', () => {
    expect(mount(M3Button, { props: { block: true } }).classes()).toContain('is-block')
    expect(mount(M3Button).classes()).not.toContain('is-block')
  })

  it('M3Button shows leading icon, replaced by spinner while loading', () => {
    const withIcon = mount(M3Button, { props: { icon: 'send' }, slots: { default: '送出' } })
    expect(withIcon.get('.m3-icon').text()).toBe('send')
    expect(withIcon.classes()).toContain('has-icon')
    expect(withIcon.find('.m3-spinner').exists()).toBe(false)

    const loading = mount(M3Button, { props: { icon: 'send', loading: true }, slots: { default: '送出' } })
    expect(loading.find('.m3-icon').exists()).toBe(false)
    expect(loading.find('.m3-spinner').attributes('aria-hidden')).toBe('true')
    expect(loading.text()).toBe('送出')

    // 沒有圖示時 loading 也補上轉圈
    const noIcon = mount(M3Button, { props: { loading: true }, slots: { default: '送出' } })
    expect(noIcon.find('.m3-spinner').exists()).toBe(true)
    expect(noIcon.classes()).toContain('has-icon')

    const plain = mount(M3Button, { slots: { default: '確認' } })
    expect(plain.find('.m3-icon').exists()).toBe(false)
    expect(plain.classes()).not.toContain('has-icon')
  })
})
