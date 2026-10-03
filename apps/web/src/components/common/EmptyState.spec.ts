import { describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import { mountWithApp } from '@/test/helpers'
import EmptyState from './EmptyState.vue'

describe('EmptyState', () => {
  it('EmptyState renders texts and action', async () => {
    const { wrapper } = await mountWithApp(EmptyState, {
      props: { title: '還沒有學生', description: '點右上角新增或匯入 Excel' },
      slots: { action: '<button>匯入 Excel</button>' },
    })

    expect(wrapper.text()).toContain('還沒有學生')
    expect(wrapper.text()).toContain('點右上角新增或匯入 Excel')
    expect(wrapper.find('button').text()).toBe('匯入 Excel')
    expect(wrapper.attributes('role')).toBe('status')
  })

  it('EmptyState omits description and action containers when absent', async () => {
    const { wrapper } = await mountWithApp(EmptyState, { props: { title: '沒有符合條件的學生' } })

    expect(wrapper.find('[data-test=empty-description]').exists()).toBe(false)
    expect(wrapper.find('[data-test=empty-action]').exists()).toBe(false)
  })

  it('EmptyState applies variant class', async () => {
    const error = await mountWithApp(EmptyState, { props: { title: '載入失敗', variant: 'error' } })
    expect(error.wrapper.classes()).toContain('is-error')
    expect(error.wrapper.find('[data-test=empty-icon]').exists()).toBe(true)

    const inline = await mountWithApp(EmptyState, { props: { title: '沒有資料', variant: 'inline' } })
    expect(inline.wrapper.classes()).toContain('is-inline')
    expect(inline.wrapper.find('[data-test=empty-icon]').exists()).toBe(false)

    const plain = await mountWithApp(EmptyState, { props: { title: '還沒有考試' } })
    expect(plain.wrapper.classes()).not.toContain('is-error')
    expect(plain.wrapper.classes()).not.toContain('is-inline')
    expect(plain.wrapper.find('[data-test=empty-icon]').exists()).toBe(true)
  })

  it('EmptyState uses default icons per variant and accepts a custom icon', async () => {
    const plain = await mountWithApp(EmptyState, { props: { title: '還沒有考試' } })
    expect(plain.wrapper.find('[data-test=empty-icon]').attributes('data-icon')).toBe('FolderOpened')

    const error = await mountWithApp(EmptyState, { props: { title: '載入失敗', variant: 'error' } })
    expect(error.wrapper.find('[data-test=empty-icon]').attributes('data-icon')).toBe('WarningFilled')

    const Custom = defineComponent({ name: 'CustomIcon', render: () => h('svg', { class: 'custom-icon' }) })
    const custom = await mountWithApp(EmptyState, { props: { title: '還沒有班級', icon: Custom } })
    expect(custom.wrapper.find('[data-test=empty-icon] .custom-icon').exists()).toBe(true)
  })
})
