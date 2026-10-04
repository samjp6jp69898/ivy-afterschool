import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ArrivalTimePicker from './ArrivalTimePicker.vue'

const ERROR_TEXT = '預計抵達時間不能早於現在'

// 台北 = UTC+8
function taipei(hhmmss: string): Date {
  const [h, m, s = '0'] = hhmmss.split(':')
  const utc = new Date(Date.UTC(2026, 9, 2, Number(h) - 8, Number(m), Number(s)))
  return utc
}

function mountPicker(now: Date | undefined, modelValue: string | null = null): VueWrapper {
  return mount(ArrivalTimePicker, { props: { modelValue, ...(now ? { now } : {}) } })
}

function chip(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll('button.m3-chip').find((b) => b.text().includes(label))
  if (!found) throw new Error(`找不到 chip ${label}`)
  return found
}

function lastEmit(wrapper: VueWrapper): unknown[] | undefined {
  const all = wrapper.emitted('update:modelValue')
  return all?.[all.length - 1]
}

afterEach(() => {
  vi.useRealTimers()
})

describe('ArrivalTimePicker', () => {
  it('ArrivalTimePicker relative option computes HH:MM', async () => {
    const wrapper = mountPicker(new Date('2026-10-02T09:31:20Z'))
    await chip(wrapper, '約 10 分鐘後').trigger('click')
    expect(lastEmit(wrapper)).toEqual(['17:42'])
    expect(chip(wrapper, '約 10 分鐘後').text()).toContain('17:42')
    expect(chip(wrapper, '約 20 分鐘後').text()).toContain('17:52')
  })

  it('ArrivalTimePicker unspecified emits null', async () => {
    const wrapper = mountPicker(new Date('2026-10-02T09:31:20Z'))
    await chip(wrapper, '約 10 分鐘後').trigger('click')
    await chip(wrapper, '不指定').trigger('click')
    expect(lastEmit(wrapper)).toEqual([null])
    expect(chip(wrapper, '不指定').attributes('aria-pressed')).toBe('true')
  })

  it('ArrivalTimePicker defaults to unspecified without emitting', () => {
    const wrapper = mountPicker(taipei('17:31:20'))
    expect(chip(wrapper, '不指定').attributes('aria-pressed')).toBe('true')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(wrapper.find('input[type="time"]').exists()).toBe(false)
  })

  it('ArrivalTimePicker custom time validation', async () => {
    const wrapper = mountPicker(new Date('2026-10-02T09:31:20Z'))
    await chip(wrapper, '自選時間').trigger('click')
    await wrapper.get('input[type="time"]').setValue('17:00')
    expect(wrapper.text()).toContain(ERROR_TEXT)
    expect(lastEmit(wrapper)).toEqual([null])
    await wrapper.get('input[type="time"]').setValue('18:15')
    expect(lastEmit(wrapper)).toEqual(['18:15'])
    expect(wrapper.text()).not.toContain(ERROR_TEXT)
  })

  it('ArrivalTimePicker custom time in the current minute is allowed and clearing emits null', async () => {
    const wrapper = mountPicker(taipei('17:31:50'))
    await chip(wrapper, '自選時間').trigger('click')
    await wrapper.get('input[type="time"]').setValue('17:31')
    expect(lastEmit(wrapper)).toEqual(['17:31'])
    expect(wrapper.text()).not.toContain(ERROR_TEXT)
    await wrapper.get('input[type="time"]').setValue('')
    expect(lastEmit(wrapper)).toEqual([null])
    expect(wrapper.text()).not.toContain(ERROR_TEXT)
  })

  it('ArrivalTimePicker disables options across midnight', () => {
    const wrapper = mountPicker(taipei('23:45:00'))
    expect(chip(wrapper, '約 20 分鐘後').attributes('disabled')).toBeDefined()
    expect(chip(wrapper, '約 30 分鐘後').attributes('disabled')).toBeDefined()
    expect(chip(wrapper, '約 10 分鐘後').attributes('disabled')).toBeUndefined()
    expect(chip(wrapper, '約 10 分鐘後').text()).toContain('23:55')
    expect(chip(wrapper, '約 20 分鐘後').text()).not.toMatch(/\d\d:\d\d/)
  })

  it('ArrivalTimePicker custom prefills ten minutes', async () => {
    const wrapper = mountPicker(new Date('2026-10-02T09:31:20Z'))
    await chip(wrapper, '自選時間').trigger('click')
    expect((wrapper.get('input[type="time"]').element as HTMLInputElement).value).toBe('17:42')
    expect(lastEmit(wrapper)).toEqual(['17:42'])

    const late = mountPicker(taipei('23:52:30'))
    await chip(late, '自選時間').trigger('click')
    expect((late.get('input[type="time"]').element as HTMLInputElement).value).toBe('')
    expect(lastEmit(late)).toEqual([null])
  })

  it('ArrivalTimePicker recomputes when now changes', async () => {
    const wrapper = mountPicker(taipei('17:31:20'))
    await chip(wrapper, '約 10 分鐘後').trigger('click')
    await wrapper.setProps({ now: taipei('17:36:20') })
    expect(chip(wrapper, '約 10 分鐘後').text()).toContain('17:47')
    expect(lastEmit(wrapper)).toEqual(['17:47'])

    const custom = mountPicker(taipei('17:31:20'))
    await chip(custom, '自選時間').trigger('click')
    await custom.get('input[type="time"]').setValue('17:40')
    await custom.setProps({ now: taipei('17:41:00') })
    expect(custom.text()).toContain(ERROR_TEXT)
    expect(lastEmit(custom)).toEqual([null])
  })

  it('ArrivalTimePicker falls back when option crosses midnight', async () => {
    const wrapper = mountPicker(taipei('23:45:00'))
    await chip(wrapper, '約 10 分鐘後').trigger('click')
    expect(lastEmit(wrapper)).toEqual(['23:55'])
    await wrapper.setProps({ now: taipei('23:50:00') })
    expect(chip(wrapper, '約 10 分鐘後').attributes('disabled')).toBeDefined()
    expect(chip(wrapper, '不指定').attributes('aria-pressed')).toBe('true')
    expect(lastEmit(wrapper)).toEqual([null])
  })

  it('ArrivalTimePicker ticks every minute without now prop', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-02T09:31:20Z'))
    const wrapper = mountPicker(undefined)
    await chip(wrapper, '約 10 分鐘後').trigger('click')
    expect(lastEmit(wrapper)).toEqual(['17:42'])
    await vi.advanceTimersByTimeAsync(60_000)
    expect(lastEmit(wrapper)).toEqual(['17:43'])
    wrapper.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('ArrivalTimePicker starts no timer when now is injected', () => {
    vi.useFakeTimers()
    const wrapper = mountPicker(taipei('17:31:20'))
    expect(vi.getTimerCount()).toBe(0)
    wrapper.unmount()
  })

  it('ArrivalTimePicker exposes an accessible group labelled by its heading', () => {
    const wrapper = mountPicker(taipei('17:31:20'))
    const group = wrapper.get('[role="group"]')
    const labelledBy = group.attributes('aria-labelledby') ?? ''
    expect(wrapper.get(`[id="${labelledBy}"]`).text()).toBe('預計抵達時間')
    expect(wrapper.findAll('button.m3-chip')).toHaveLength(5)
  })

  it('ArrivalTimePicker initializes custom time from a non-null modelValue', () => {
    const wrapper = mountPicker(taipei('17:31:20'), '18:15')
    expect(chip(wrapper, '自選時間').attributes('aria-pressed')).toBe('true')
    expect(chip(wrapper, '不指定').attributes('aria-pressed')).toBe('false')
    expect((wrapper.get('input[type="time"]').element as HTMLInputElement).value).toBe('18:15')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('ArrivalTimePicker returns to unspecified when parent clears a non-null modelValue', async () => {
    const wrapper = mountPicker(taipei('17:31:20'), '18:15')
    await wrapper.setProps({ modelValue: null })
    expect(chip(wrapper, '不指定').attributes('aria-pressed')).toBe('true')
    expect(chip(wrapper, '自選時間').attributes('aria-pressed')).toBe('false')
    expect(wrapper.find('input[type="time"]').exists()).toBe(false)
  })

  it('ArrivalTimePicker keeps custom time when parent mirrors its own null emit', async () => {
    const wrapper = mountPicker(taipei('17:31:20'), '18:15')
    await wrapper.get('input[type="time"]').setValue('17:00')
    expect(lastEmit(wrapper)).toEqual([null])
    await wrapper.setProps({ modelValue: null })
    expect(chip(wrapper, '自選時間').attributes('aria-pressed')).toBe('true')
    expect((wrapper.get('input[type="time"]').element as HTMLInputElement).value).toBe('17:00')
    expect(wrapper.text()).toContain(ERROR_TEXT)
  })

  it('ArrivalTimePicker resets to unspecified when parent clears modelValue', async () => {
    const wrapper = mountPicker(taipei('17:31:20'))
    await chip(wrapper, '約 10 分鐘後').trigger('click')
    await wrapper.setProps({ modelValue: '17:42' })
    await wrapper.setProps({ modelValue: null })
    expect(chip(wrapper, '不指定').attributes('aria-pressed')).toBe('true')
  })
})
