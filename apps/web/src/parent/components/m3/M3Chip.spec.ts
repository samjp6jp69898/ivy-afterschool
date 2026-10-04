import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import M3Chip from './M3Chip.vue'

describe('M3Chip', () => {
  it('M3Chip filter shows pressed state', () => {
    const wrapper = mount(M3Chip, { props: { variant: 'filter', selected: true, label: '約 10 分鐘' } })
    expect(wrapper.attributes('aria-pressed')).toBe('true')
    expect(wrapper.get('.m3-icon').text()).toBe('check')
    expect(wrapper.text()).toContain('約 10 分鐘')
    expect(wrapper.classes()).toContain('is-selected')

    const unselected = mount(M3Chip, { props: { variant: 'filter', label: '約 20 分鐘' } })
    expect(unselected.attributes('aria-pressed')).toBe('false')
    expect(unselected.find('.m3-icon').exists()).toBe(false)
    expect(unselected.classes()).not.toContain('is-selected')
  })

  it('M3Chip emits click unless disabled', async () => {
    const wrapper = mount(M3Chip, { props: { label: '撥打安親班' } })
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)

    const disabled = mount(M3Chip, { props: { label: '撥打安親班', disabled: true } })
    await disabled.trigger('click')
    expect(disabled.emitted('click')).toBeUndefined()
    expect(disabled.attributes('disabled')).toBeDefined()
  })

  it('M3Chip assist has no aria-pressed and shows its own icon', () => {
    const wrapper = mount(M3Chip, { props: { label: '撥打安親班', icon: 'call' } })
    expect(wrapper.attributes('aria-pressed')).toBeUndefined()
    expect(wrapper.get('.m3-icon').text()).toBe('call')
    expect(wrapper.classes()).toContain('m3-chip--assist')
    expect(wrapper.attributes('type')).toBe('button')
  })

  it('M3Chip selected only takes effect on filter variant', () => {
    const wrapper = mount(M3Chip, { props: { label: '查看作業', selected: true } })
    expect(wrapper.classes()).not.toContain('is-selected')
    expect(wrapper.find('.m3-icon').exists()).toBe(false)
  })

  it('M3Chip filter selected replaces its own icon with check', () => {
    const wrapper = mount(M3Chip, { props: { variant: 'filter', selected: true, label: '未讀', icon: 'call' } })
    expect(wrapper.findAll('.m3-icon')).toHaveLength(1)
    expect(wrapper.get('.m3-icon').text()).toBe('check')
  })
})
