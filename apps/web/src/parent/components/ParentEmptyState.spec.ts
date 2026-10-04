import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ParentEmptyState from './ParentEmptyState.vue'
import source from './ParentEmptyState.vue?raw'

describe('ParentEmptyState', () => {
  it('ParentEmptyState empty with action', async () => {
    const wrapper = mount(ParentEmptyState, { props: { title: '尚未新增常用接送人', actionLabel: '新增接送人' } })
    expect(wrapper.text()).toContain('尚未新增常用接送人')
    expect(wrapper.text()).toContain('新增接送人')
    expect(wrapper.attributes('role')).toBeUndefined()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('action')).toHaveLength(1)
  })

  it('ParentEmptyState error defaults retry', async () => {
    const wrapper = mount(ParentEmptyState, { props: { variant: 'error', title: '載入失敗' } })
    expect(wrapper.attributes('role')).toBe('alert')
    const button = wrapper.get('button')
    expect(button.text()).toContain('重試')
    await button.trigger('click')
    expect(wrapper.emitted('action')).toHaveLength(1)
  })

  it('ParentEmptyState empty without actionLabel renders no button and shows description', () => {
    const wrapper = mount(ParentEmptyState, { props: { title: '目前沒有作業', description: '老師登記後會顯示在這裡' } })
    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.text()).toContain('老師登記後會顯示在這裡')
  })

  it('ParentEmptyState uses default icons per variant and a custom icon override', async () => {
    const empty = mount(ParentEmptyState, { props: { title: 'a' } })
    expect(empty.get('.empty-state__icon').text()).toBe('inbox')
    expect(empty.classes()).toContain('is-empty')
    const error = mount(ParentEmptyState, { props: { variant: 'error', title: 'a' } })
    expect(error.get('.empty-state__icon').text()).toBe('error')
    expect(error.classes()).toContain('is-error')
    await empty.setProps({ icon: 'folder_open' })
    expect(empty.get('.empty-state__icon').text()).toBe('folder_open')
  })

  it('ParentEmptyState error custom actionLabel overrides retry and title is a paragraph', () => {
    const wrapper = mount(ParentEmptyState, { props: { variant: 'error', title: '無權限', actionLabel: '回首頁' } })
    expect(wrapper.get('button').text()).toContain('回首頁')
    expect(wrapper.text()).not.toContain('重試')
    expect(wrapper.find('h1,h2,h3').exists()).toBe(false)
    expect(wrapper.get('.empty-state__title').element.tagName).toBe('P')
  })

  it('ParentEmptyState style follows design decisions', () => {
    expect(source).toMatch(/padding:\s*32px 16px/)
    expect(source).toMatch(/max-width:\s*280px/)
    expect(source).toMatch(/overflow-wrap:\s*anywhere/)
    expect(source).toMatch(/\.is-empty \.empty-state__icon\s*\{[^}]*var\(--m3-outline\)/)
    expect(source).toMatch(/\.is-error \.empty-state__icon\s*\{[^}]*var\(--m3-error\)/)
  })
})
