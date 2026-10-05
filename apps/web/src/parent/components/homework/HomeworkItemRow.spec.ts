import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import HomeworkItemRow from './HomeworkItemRow.vue'

describe('HomeworkItemRow', () => {
  it('HomeworkItemRow shows subject title status', () => {
    const wrapper = mount(HomeworkItemRow, {
      props: { item: { title: '數學習作 p.12-13', subject_name: '數學', status: 'correcting' } },
    })

    expect(wrapper.element.tagName).toBe('LI')
    expect(wrapper.get('.hw-row__subject').text()).toBe('數學')
    expect(wrapper.get('.hw-row__title').text()).toBe('數學習作 p.12-13')
    expect(wrapper.get('.status-pill').text()).toBe('訂正中')
    expect(wrapper.get('.status-pill').classes()).toContain('status-pill--danger')
    expect(wrapper.find('.m3-icon').exists()).toBe(false)
    expect(wrapper.classes()).not.toContain('is-done')
  })

  it('HomeworkItemRow fallback subject and done style', () => {
    const wrapper = mount(HomeworkItemRow, {
      props: { item: { title: '自然學習單', subject_name: null, status: 'done' } },
    })

    expect(wrapper.get('.hw-row__subject').text()).toBe('其他')
    expect(wrapper.get('.status-pill__label').text()).toBe('完成')
    expect(wrapper.get('.status-pill').classes()).toContain('status-pill--success')
    const icon = wrapper.get('.status-pill .m3-icon')
    expect(icon.text()).toBe('check')
    expect(icon.attributes('aria-hidden')).toBe('true')
    expect(wrapper.classes()).toContain('is-done')
  })

  it('HomeworkItemRow maps todo and doing tones', () => {
    const todo = mount(HomeworkItemRow, { props: { item: { title: 'a', subject_name: '國語', status: 'todo' } } })
    const doing = mount(HomeworkItemRow, { props: { item: { title: 'b', subject_name: '英語', status: 'doing' } } })

    expect(todo.get('.status-pill').text()).toBe('未開始')
    expect(todo.get('.status-pill').classes()).toContain('status-pill--info')
    expect(doing.get('.status-pill').text()).toBe('進行中')
    expect(doing.get('.status-pill').classes()).toContain('status-pill--warning')
  })
})
