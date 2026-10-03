import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import M3Switch from './M3Switch.vue'

function keyEvent(type: 'keydown' | 'keyup', key: string, init: KeyboardEventInit = {}): KeyboardEvent {
  return new KeyboardEvent(type, { key, cancelable: true, bubbles: true, ...init })
}

describe('M3Switch', () => {
  it('M3Switch toggles and emits', async () => {
    const wrapper = mount(M3Switch, { props: { modelValue: false, label: '作業完成' } })
    expect(wrapper.element.tagName).toBe('BUTTON')
    expect(wrapper.attributes('type')).toBe('button')
    expect(wrapper.attributes('role')).toBe('switch')
    expect(wrapper.attributes('aria-checked')).toBe('false')
    expect(wrapper.attributes('aria-label')).toBe('作業完成')
    await wrapper.trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([true])

    await wrapper.setProps({ modelValue: true })
    expect(wrapper.attributes('aria-checked')).toBe('true')
    await wrapper.trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[1]).toEqual([false])
  })

  it('M3Switch blocked when disabled or busy', async () => {
    const disabled = mount(M3Switch, { props: { modelValue: false, label: '到班通知', disabled: true } })
    expect(disabled.attributes('disabled')).toBeDefined()
    await disabled.trigger('click')
    expect(disabled.emitted('update:modelValue')).toBeUndefined()

    const busy = mount(M3Switch, { props: { modelValue: false, label: '到班通知', busy: true } })
    await busy.trigger('click')
    busy.element.dispatchEvent(keyEvent('keydown', ' '))
    busy.element.dispatchEvent(keyEvent('keydown', 'Enter'))
    expect(busy.emitted('update:modelValue')).toBeUndefined()
    expect(busy.attributes('aria-busy')).toBe('true')
    // busy 不用原生 disabled：保留焦點、不淡化
    expect(busy.attributes('disabled')).toBeUndefined()
    expect(mount(M3Switch, { props: { modelValue: false, label: '到班通知' } }).attributes('aria-busy')).toBeUndefined()
  })

  it('M3Switch keyboard toggle', () => {
    const wrapper = mount(M3Switch, { props: { modelValue: false, label: '作業完成' } })
    const space = keyEvent('keydown', ' ')
    wrapper.element.dispatchEvent(space)
    expect(wrapper.emitted('update:modelValue')).toEqual([[true]])
    // 攔下原生按鈕的 Enter / Space 啟動，避免 keydown 與原生 click 各切換一次
    expect(space.defaultPrevented).toBe(true)
    const spaceUp = keyEvent('keyup', ' ')
    wrapper.element.dispatchEvent(spaceUp)
    expect(spaceUp.defaultPrevented).toBe(true)

    const enter = keyEvent('keydown', 'Enter')
    wrapper.element.dispatchEvent(enter)
    expect(enter.defaultPrevented).toBe(true)
    expect(wrapper.emitted('update:modelValue')).toHaveLength(2)

    // 按住不放的重複 keydown 不連續切換
    wrapper.element.dispatchEvent(keyEvent('keydown', ' ', { repeat: true }))
    wrapper.element.dispatchEvent(keyEvent('keydown', 'a'))
    expect(wrapper.emitted('update:modelValue')).toHaveLength(2)
  })

  it('M3Switch shows check when on', () => {
    const on = mount(M3Switch, { props: { modelValue: true, label: '作業完成' } })
    expect(on.classes()).toContain('is-on')
    expect(on.get('.m3-switch__thumb .m3-icon').text()).toBe('check')

    const off = mount(M3Switch, { props: { modelValue: false, label: '作業完成' } })
    expect(off.classes()).not.toContain('is-on')
    expect(off.find('.m3-switch__thumb .m3-icon').exists()).toBe(false)
  })

  it('M3Switch busy keeps thumb position and shows spinner', () => {
    const busyOn = mount(M3Switch, { props: { modelValue: true, label: '作業完成', busy: true } })
    expect(busyOn.classes()).toEqual(expect.arrayContaining(['is-on', 'is-busy']))
    expect(busyOn.find('.m3-switch__thumb .m3-switch__spinner').exists()).toBe(true)
    expect(busyOn.find('.m3-switch__thumb .m3-icon').exists()).toBe(false)

    const busyOff = mount(M3Switch, { props: { modelValue: false, label: '作業完成', busy: true } })
    expect(busyOff.classes()).toContain('is-busy')
    expect(busyOff.classes()).not.toContain('is-on')
    expect(busyOff.find('.m3-switch__spinner').exists()).toBe(true)

    const idle = mount(M3Switch, { props: { modelValue: true, label: '作業完成' } })
    expect(idle.find('.m3-switch__spinner').exists()).toBe(false)
  })
})
