import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import M3SegmentedButton from './M3SegmentedButton.vue'

const ITEMS = [
  { value: 'sick', label: '病假' },
  { value: 'personal', label: '事假' },
  { value: 'other', label: '其他' },
]

function mountSeg(props: Record<string, unknown> = {}, attachTo?: HTMLElement) {
  return mount(M3SegmentedButton, {
    props: { modelValue: 'sick', items: ITEMS, ariaLabel: '假別', ...props },
    attachTo,
  })
}

describe('M3SegmentedButton', () => {
  it('M3SegmentedButton marks checked and emits', async () => {
    const wrapper = mountSeg()
    const radios = wrapper.findAll('[role="radio"]')
    expect(radios).toHaveLength(3)
    expect(radios[0]?.attributes('aria-checked')).toBe('true')
    expect(radios[1]?.attributes('aria-checked')).toBe('false')
    expect(radios[2]?.attributes('aria-checked')).toBe('false')
    await radios[1]?.trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['personal'])
  })

  it('M3SegmentedButton is a labelled radiogroup with roving tabindex', () => {
    const wrapper = mountSeg({ modelValue: 'personal' })
    expect(wrapper.attributes('role')).toBe('radiogroup')
    expect(wrapper.attributes('aria-label')).toBe('假別')
    const tabindexes = wrapper.findAll('[role="radio"]').map((r) => r.attributes('tabindex'))
    expect(tabindexes).toEqual(['-1', '0', '-1'])
  })

  it('M3SegmentedButton falls back to first enabled segment for tab order when value is unknown', () => {
    const items = [
      { value: 'sick', label: '病假', disabled: true },
      { value: 'personal', label: '事假' },
    ]
    const wrapper = mountSeg({ modelValue: '', items })
    expect(wrapper.findAll('[role="radio"]').map((r) => r.attributes('tabindex'))).toEqual(['-1', '0'])
  })

  it('M3SegmentedButton arrow keys skip disabled', async () => {
    const items = [
      { value: 'sick', label: '病假' },
      { value: 'personal', label: '事假', disabled: true },
      { value: 'other', label: '其他' },
    ]
    const host = document.createElement('div')
    document.body.appendChild(host)
    const wrapper = mountSeg({ items }, host)
    const radios = wrapper.findAll('[role="radio"]')
    const event = new KeyboardEvent('keydown', { key: 'ArrowRight', cancelable: true, bubbles: true })
    radios[0]?.element.dispatchEvent(event)
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['other'])
    expect(event.defaultPrevented).toBe(true)
    wrapper.unmount()
    host.remove()
  })

  it('M3SegmentedButton arrow keys wrap around both ends and move focus', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const wrapper = mountSeg({ modelValue: 'other' }, host)
    const radios = wrapper.findAll('[role="radio"]')
    await radios[2]?.trigger('keydown', { key: 'ArrowRight' })
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['sick'])

    await wrapper.setProps({ modelValue: 'sick' })
    await radios[0]?.trigger('keydown', { key: 'ArrowLeft' })
    expect(wrapper.emitted('update:modelValue')?.[1]).toEqual(['other'])
    await wrapper.setProps({ modelValue: 'other' })
    expect(document.activeElement).toBe(radios[2]?.element)

    await radios[2]?.trigger('keydown', { key: 'Home' })
    expect(wrapper.emitted('update:modelValue')).toHaveLength(2)
    wrapper.unmount()
    host.remove()
  })

  it('M3SegmentedButton arrow key does nothing when no other segment is enabled', async () => {
    const items = [
      { value: 'sick', label: '病假' },
      { value: 'personal', label: '事假', disabled: true },
    ]
    const wrapper = mountSeg({ items })
    await wrapper.findAll('[role="radio"]')[0]?.trigger('keydown', { key: 'ArrowRight' })
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('M3SegmentedButton disabled segment not clickable', async () => {
    const items = [
      { value: 'sick', label: '病假' },
      { value: 'personal', label: '事假', disabled: true },
    ]
    const wrapper = mountSeg({ items })
    const disabled = wrapper.findAll('[role="radio"]')[1]
    await disabled?.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(disabled?.attributes('disabled')).toBeDefined()
  })

  it('M3SegmentedButton shows check on selected segment and its own icon on others', () => {
    const items = [
      { value: 'saved', label: '常用接送人', icon: 'group' },
      { value: 'adhoc', label: '臨時填寫', icon: 'edit' },
    ]
    const wrapper = mountSeg({ modelValue: 'saved', items })
    const [first, second] = wrapper.findAll('[role="radio"]')
    expect(first?.get('.m3-icon').text()).toBe('check')
    expect(first?.classes()).toContain('is-checked')
    expect(second?.get('.m3-icon').text()).toBe('edit')
    expect(second?.classes()).not.toContain('is-checked')

    const plain = mountSeg({ modelValue: 'other' })
    expect(plain.findAll('[role="radio"]')[0]?.find('.m3-icon').exists()).toBe(false)
    expect(plain.findAll('[role="radio"]')[2]?.get('.m3-icon').text()).toBe('check')
  })

  it('M3SegmentedButton lays out equal columns by item count', () => {
    expect(mountSeg().attributes('style')).toContain('repeat(3, minmax(0, 1fr))')
    expect(mountSeg({ items: ITEMS.slice(0, 2) }).attributes('style')).toContain('repeat(2, minmax(0, 1fr))')
  })
})
