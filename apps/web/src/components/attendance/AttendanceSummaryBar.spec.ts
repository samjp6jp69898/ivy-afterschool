import { describe, expect, it } from 'vitest'
import { mountWithApp } from '@/test/helpers'
import AttendanceSummaryBar from './AttendanceSummaryBar.vue'

const SUMMARY = { total: 20, expected: 5, present: 10, left: 2, absent: 1, leave: 2 }

async function mountBar(props: Record<string, unknown> = {}) {
  const { wrapper } = await mountWithApp(AttendanceSummaryBar, {
    props: { summary: SUMMARY, active: null, ...props },
  })
  return wrapper
}

/** 每張 chip 的「標籤 數值」文字，例如 '預計到班 5' */
function chipTexts(wrapper: Awaited<ReturnType<typeof mountBar>>): string[] {
  return wrapper.findAll('[role=button]').map((chip) => {
    const label = chip.find('.stat-card__label').text()
    return `${label} ${chip.find('[data-test=stat-value]').text()}`
  })
}

function chip(wrapper: Awaited<ReturnType<typeof mountBar>>, label: string) {
  const found = wrapper.findAll('[role=button]').find((c) => c.find('.stat-card__label').text() === label)
  if (!found) throw new Error(`找不到 chip「${label}」`)
  return found
}

describe('AttendanceSummaryBar', () => {
  it('AttendanceSummaryBar renders counts and rate', async () => {
    const wrapper = await mountBar()

    expect(chipTexts(wrapper)).toEqual([
      '全部 20',
      '預計到班 5',
      '已到班 10',
      '已離班 2',
      '缺席 1',
      '請假 2',
    ])
    expect(wrapper.find('[data-test=attendance-rate]').text()).toContain('出席率')
    expect(wrapper.find('[data-test=attendance-rate] [data-test=stat-value]').text()).toBe('67%')
  })

  it('AttendanceSummaryBar colors chips by attendance status tone', async () => {
    const wrapper = await mountBar()

    expect(chip(wrapper, '預計到班').classes()).toContain('tone-warning')
    expect(chip(wrapper, '已到班').classes()).toContain('tone-success')
    expect(chip(wrapper, '缺席').classes()).toContain('tone-danger')
    expect(chip(wrapper, '已離班').classes()).toContain('tone-info')
  })

  it('AttendanceSummaryBar toggles active status', async () => {
    const wrapper = await mountBar()
    await chip(wrapper, '缺席').trigger('click')
    expect(wrapper.emitted('update:active')?.[0]).toEqual(['absent'])

    await wrapper.setProps({ active: 'absent' })
    expect(chip(wrapper, '缺席').attributes('aria-pressed')).toBe('true')
    expect(chip(wrapper, '全部').attributes('aria-pressed')).toBe('false')
    await chip(wrapper, '缺席').trigger('click')
    expect(wrapper.emitted('update:active')?.[1]).toEqual([null])

    await chip(wrapper, '已到班').trigger('click')
    expect(wrapper.emitted('update:active')?.[2]).toEqual(['present'])
  })

  it('AttendanceSummaryBar all chip is selected when active is null and emits null', async () => {
    const wrapper = await mountBar({ active: 'present' })
    expect(chip(wrapper, '全部').attributes('aria-pressed')).toBe('false')
    await chip(wrapper, '全部').trigger('click')
    expect(wrapper.emitted('update:active')?.[0]).toEqual([null])

    await wrapper.setProps({ active: null })
    expect(chip(wrapper, '全部').attributes('aria-pressed')).toBe('true')
  })

  it('AttendanceSummaryBar handles zero denominator', async () => {
    const wrapper = await mountBar({
      summary: { total: 2, expected: 0, present: 0, left: 0, absent: 0, leave: 2 },
    })

    expect(wrapper.find('[data-test=attendance-rate] [data-test=stat-value]').text()).toBe('—')
  })

  it('AttendanceSummaryBar rounds rate to an integer percent', async () => {
    const wrapper = await mountBar({
      summary: { total: 3, expected: 1, present: 1, left: 0, absent: 1, leave: 0 },
    })

    expect(wrapper.find('[data-test=attendance-rate] [data-test=stat-value]').text()).toBe('33%')
  })
})
