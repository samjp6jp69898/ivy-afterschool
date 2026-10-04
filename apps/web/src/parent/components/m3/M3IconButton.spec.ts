import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import M3IconButton from './M3IconButton.vue'

describe('M3IconButton', () => {
  it('M3IconButton exposes aria label and emits', async () => {
    const wrapper = mount(M3IconButton, { props: { icon: 'arrow_back', label: '返回' } })
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
    expect(wrapper.emitted('click')?.[0]?.[0]).toBeInstanceOf(MouseEvent)
    expect(wrapper.attributes('aria-label')).toBe('返回')
    expect(wrapper.attributes('type')).toBe('button')
    expect(wrapper.get('.m3-icon').text()).toBe('arrow_back')
  })

  it('M3IconButton disabled blocks click', async () => {
    const wrapper = mount(M3IconButton, { props: { icon: 'done_all', label: '全部標為已讀', disabled: true } })
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
    expect(wrapper.attributes('disabled')).toBeDefined()
  })

  it('M3IconButton applies variant class and defaults to standard', () => {
    expect(mount(M3IconButton, { props: { icon: 'add', label: '新增' } }).classes()).toContain('m3-icon-button--standard')
    expect(mount(M3IconButton, { props: { icon: 'add', label: '新增', variant: 'tonal' } }).classes()).toContain('m3-icon-button--tonal')
    expect(mount(M3IconButton, { props: { icon: 'add', label: '新增', variant: 'filled' } }).classes()).toContain('m3-icon-button--filled')
  })

  it('M3IconButton icon is decorative so the accessible name comes only from label', () => {
    const wrapper = mount(M3IconButton, { props: { icon: 'add', label: '新增接送人' } })
    expect(wrapper.get('.m3-icon').attributes('aria-hidden')).toBe('true')
    expect(wrapper.get('.m3-icon').attributes('aria-label')).toBeUndefined()
  })
})
