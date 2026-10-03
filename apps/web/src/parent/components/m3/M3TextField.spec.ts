import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import M3TextField from './M3TextField.vue'

describe('M3TextField', () => {
  it('M3TextField emits update on input', async () => {
    const wrapper = mount(M3TextField, { props: { label: '綁定碼' } })
    await wrapper.get('input').setValue('abcd')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['abcd'])
    const label = wrapper.get('label')
    expect(label.text()).toBe('綁定碼')
    const inputId = wrapper.get('input').attributes('id')
    expect(inputId).toMatch(/\S/)
    expect(label.attributes('for')).toBe(inputId)
  })

  it('M3TextField uses given id and generates unique ids', () => {
    const given = mount(M3TextField, { props: { label: '接送人姓名', id: 'proxy-name' } })
    expect(given.get('input').attributes('id')).toBe('proxy-name')
    expect(given.get('label').attributes('for')).toBe('proxy-name')

    const a = mount(M3TextField, { props: { label: 'A' } }).get('input').attributes('id')
    const b = mount(M3TextField, { props: { label: 'B' } }).get('input').attributes('id')
    expect(a).not.toBe(b)
  })

  it('M3TextField shows error state', () => {
    const wrapper = mount(M3TextField, {
      props: {
        label: '綁定碼',
        modelValue: 'AB12',
        supportingText: '請輸入安親班提供的 8 碼綁定碼',
        errorText: '綁定碼格式錯誤',
      },
    })
    expect(wrapper.text()).toContain('綁定碼格式錯誤')
    // errorText 取代 supportingText
    expect(wrapper.text()).not.toContain('請輸入安親班提供的 8 碼綁定碼')
    const input = wrapper.get('input')
    expect(input.attributes('aria-invalid')).toBe('true')
    const describedBy = input.attributes('aria-describedby')
    expect(describedBy).toMatch(/\S/)
    expect(wrapper.get(`[id="${describedBy}"]`).text()).toBe('綁定碼格式錯誤')
    expect(wrapper.classes()).toContain('is-error')
    expect(wrapper.get('.m3-tf__trailing').text()).toBe('error')
  })

  it('M3TextField supporting text and valid state', () => {
    const wrapper = mount(M3TextField, {
      props: { label: '綁定碼', supportingText: '請輸入安親班提供的 8 碼綁定碼' },
    })
    const input = wrapper.get('input')
    expect(input.attributes('aria-invalid')).toBe('false')
    const describedBy = input.attributes('aria-describedby')
    expect(wrapper.get(`[id="${describedBy}"]`).text()).toBe('請輸入安親班提供的 8 碼綁定碼')
    expect(wrapper.find('.m3-tf__trailing').exists()).toBe(false)

    const bare = mount(M3TextField, { props: { label: '綁定碼' } })
    expect(bare.get('input').attributes('aria-describedby')).toBeUndefined()
    expect(bare.find('.m3-tf__supporting').exists()).toBe(false)
  })

  it('M3TextField textarea counter', () => {
    const wrapper = mount(M3TextField, {
      props: { label: '事由', type: 'textarea', maxlength: 200, modelValue: '生病' },
    })
    expect(wrapper.find('input').exists()).toBe(false)
    const textarea = wrapper.get('textarea')
    expect(textarea.element.value).toBe('生病')
    expect(textarea.attributes('maxlength')).toBe('200')
    expect(wrapper.get('.m3-tf__counter').text()).toBe('2/200')
    expect(wrapper.get('label').attributes('for')).toBe(textarea.attributes('id'))
  })

  it('M3TextField counter for input with maxlength', () => {
    const wrapper = mount(M3TextField, { props: { label: '綁定碼', maxlength: 8, modelValue: 'AB12' } })
    expect(wrapper.get('.m3-tf__counter').text()).toBe('4/8')
    expect(mount(M3TextField, { props: { label: '綁定碼' } }).find('.m3-tf__counter').exists()).toBe(false)
    expect(mount(M3TextField, { props: { label: '綁定碼', maxlength: 8 } }).get('.m3-tf__counter').text()).toBe(
      '0/8',
    )
  })

  it('M3TextField emits enter', async () => {
    const wrapper = mount(M3TextField, { props: { label: '綁定碼' } })
    await wrapper.get('input').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('enter')).toHaveLength(1)
    await wrapper.get('input').trigger('keydown', { key: 'a' })
    expect(wrapper.emitted('enter')).toHaveLength(1)
  })

  it('M3TextField ignores enter while composing', async () => {
    const wrapper = mount(M3TextField, { props: { label: '接送人姓名' } })
    await wrapper.get('input').trigger('keydown', { key: 'Enter', isComposing: true })
    await wrapper.get('input').trigger('keydown', { key: 'Enter', keyCode: 229 })
    expect(wrapper.emitted('enter')).toBeUndefined()

    const textarea = mount(M3TextField, { props: { label: '事由', type: 'textarea' } })
    await textarea.get('textarea').trigger('keydown', { key: 'Enter' })
    expect(textarea.emitted('enter')).toBeUndefined()
  })

  it('M3TextField floating label', async () => {
    const empty = mount(M3TextField, { props: { label: '接送人姓名' } })
    expect(empty.classes()).not.toContain('is-floating')
    await empty.get('input').trigger('focus')
    expect(empty.classes()).toEqual(expect.arrayContaining(['is-floating', 'is-focused']))
    await empty.get('input').trigger('blur')
    expect(empty.classes()).not.toContain('is-floating')

    expect(mount(M3TextField, { props: { label: '接送人姓名', modelValue: '王大明' } }).classes()).toContain(
      'is-floating',
    )
    expect(mount(M3TextField, { props: { label: '請假開始日', type: 'date' } }).classes()).toContain('is-floating')
    expect(mount(M3TextField, { props: { label: '預計到達', type: 'time' } }).classes()).toContain('is-floating')
  })

  it('M3TextField placeholder only while focused', async () => {
    const wrapper = mount(M3TextField, { props: { label: '與學生關係', placeholder: '例如：外婆' } })
    const input = wrapper.get('input')
    expect(input.attributes('placeholder')).toBeUndefined()
    await input.trigger('focus')
    expect(input.attributes('placeholder')).toBe('例如：外婆')
    await input.trigger('blur')
    expect(input.attributes('placeholder')).toBeUndefined()
  })

  it('M3TextField passes input attributes and disabled', () => {
    const wrapper = mount(M3TextField, {
      props: { label: '聯絡電話', type: 'tel', inputmode: 'tel', autocomplete: 'tel', disabled: true },
    })
    const input = wrapper.get('input')
    expect(input.attributes('type')).toBe('tel')
    expect(input.attributes('inputmode')).toBe('tel')
    expect(input.attributes('autocomplete')).toBe('tel')
    expect(input.attributes('disabled')).toBeDefined()
    expect(wrapper.classes()).toContain('is-disabled')
  })
})
