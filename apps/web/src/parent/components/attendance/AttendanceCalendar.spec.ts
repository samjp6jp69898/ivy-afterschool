import { mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ATTENDANCE_STATUS_META } from '@/shared/constants/statusLabels'
import type { AttendanceDay } from '../../api/attendance'
import AttendanceCalendar from './AttendanceCalendar.vue'
import source from './AttendanceCalendar.vue?raw'

interface CalendarProps {
  month: string
  days: AttendanceDay[]
  today: string
  selectedDate: string | null
}

function day(date: string, overrides: Partial<AttendanceDay> = {}): AttendanceDay {
  return {
    date,
    is_service_day: true,
    status: null,
    check_in_at: null,
    check_out_at: null,
    leave_type: null,
    ...overrides,
  }
}

function mountCalendar(overrides: Partial<CalendarProps> = {}) {
  const props: CalendarProps = { month: '2026-10', days: [], today: '2026-10-22', selectedDate: null, ...overrides }
  return mount(AttendanceCalendar, { props })
}

/** 以日期數字找格子（button） */
function cell(wrapper: VueWrapper, dayNumber: number): DOMWrapper<Element> {
  const found = wrapper.findAll('button.att-day').find((b) => b.get('.att-day__num').text() === String(dayNumber))
  if (!found) throw new Error(`找不到 ${dayNumber} 日的格子`)
  return found
}

function label(wrapper: VueWrapper, dayNumber: number): string | undefined {
  return cell(wrapper, dayNumber).attributes('aria-label')
}

/** 格線的直接子節點：空白補格是 span，日期格是 button */
function gridChildren(wrapper: VueWrapper): Element[] {
  return Array.from((wrapper.get('.att-cal__grid').element as Element).children)
}

/** ?raw 原始碼中某選擇器（行首，可縮排）的樣式區塊內容 */
function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = new RegExp(`^\\s*${escaped} \\{([^}]*)\\}`, 'm').exec(source)
  expect(match, `找不到 ${selector} 區塊`).not.toBeNull()
  return match?.[1] ?? ''
}

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('AttendanceCalendar', () => {
  it('AttendanceCalendar pads first week', () => {
    // 2026-10-01 是週四：週日開頭要補 4 個空格
    const wrapper = mountCalendar({ month: '2026-10' })

    const children = gridChildren(wrapper)
    expect(children.slice(0, 4).map((el) => el.tagName)).toEqual(['SPAN', 'SPAN', 'SPAN', 'SPAN'])
    expect(children.slice(0, 4).every((el) => el.getAttribute('aria-hidden') === 'true')).toBe(true)
    expect(children[4]?.tagName).toBe('BUTTON')
    expect(children[4]?.textContent).toBe('1')
    expect(wrapper.findAll('.att-cal__grid button')).toHaveLength(31)
    expect(children).toHaveLength(35)
  })

  it('AttendanceCalendar labels status', () => {
    const wrapper = mountCalendar({
      today: '2026-10-22',
      days: [
        day('2026-10-02', { status: 'present' }),
        day('2026-10-05', { status: 'leave', leave_type: 'sick' }),
      ],
    })

    expect(label(wrapper, 2)).toBe('10月2日 已到班')
    expect(label(wrapper, 5)).toBe('10月5日 病假')
  })

  it('AttendanceCalendar marks closed day and today', () => {
    const wrapper = mountCalendar({
      today: '2026-10-02',
      days: [day('2026-10-04', { is_service_day: false })],
    })

    expect(label(wrapper, 4)).toBe('10月4日 休息')
    expect(cell(wrapper, 4).classes()).toContain('att-day--closed')
    expect(cell(wrapper, 2).attributes('aria-current')).toBe('date')
    expect(wrapper.findAll('[aria-current]')).toHaveLength(1)
  })

  it('AttendanceCalendar emits select', async () => {
    const wrapper = mountCalendar({ selectedDate: '2026-10-15' })

    await cell(wrapper, 15).trigger('click')

    expect(wrapper.emitted('select')).toHaveLength(1)
    expect(wrapper.emitted('select')?.[0]).toEqual(['2026-10-15'])
    expect(cell(wrapper, 15).attributes('aria-pressed')).toBe('true')
    expect(cell(wrapper, 14).attributes('aria-pressed')).toBe('false')
    expect(wrapper.findAll('[aria-pressed="true"]')).toHaveLength(1)
  })

  it('AttendanceCalendar shows past expected as unregistered', () => {
    const wrapper = mountCalendar({
      today: '2026-10-02',
      days: [
        day('2026-10-01', { status: 'expected' }),
        day('2026-10-02', { status: 'expected' }),
      ],
    })

    expect(label(wrapper, 1)).toBe('10月1日 未登記')
    expect(label(wrapper, 2)).toBe('10月2日 預計到班')
    expect(wrapper.get('.att-legend').text()).toContain('未登記')
    expect(cell(wrapper, 1).classes()).toContain('att-day--unrecorded')
    expect(cell(wrapper, 2).classes()).toContain('att-day--expected')
  })

  it('AttendanceCalendar distinguishes leave from left', () => {
    const wrapper = mountCalendar({
      days: [
        day('2026-10-05', { status: 'leave', leave_type: 'sick' }),
        day('2026-10-06', { status: 'left' }),
      ],
    })

    const leave = cell(wrapper, 5).classes()
    const left = cell(wrapper, 6).classes()
    expect(leave).toContain('att-day--leave')
    expect(left).toContain('att-day--left')
    expect(leave).not.toContain('att-day--left')
    expect(left).not.toContain('att-day--leave')
    // 請假格在月曆格與圖例改用 tertiary-container，與已離班（secondary-container）明顯區分
    expect(rule('.att-day--leave')).toContain('background: var(--m3-tertiary-container)')
    expect(rule('.att-day--leave')).toContain('color: var(--m3-on-tertiary-container)')
    expect(rule('.att-day--left')).toContain('background: var(--m3-secondary-container)')
    expect(rule('.att-day--left')).toContain('color: var(--m3-on-secondary-container)')
    expect(cell(wrapper, 5).get('.m3-icon').text()).toBe('event_busy')
    expect(cell(wrapper, 6).get('.m3-icon').text()).toBe('done_all')
  })

  it('AttendanceCalendar names leave types in the aria label', () => {
    const wrapper = mountCalendar({
      days: [
        day('2026-10-05', { status: 'leave', leave_type: 'sick' }),
        day('2026-10-13', { status: 'leave', leave_type: 'personal' }),
        day('2026-10-20', { status: 'leave', leave_type: 'other' }),
        day('2026-10-21', { status: 'leave', leave_type: null }),
      ],
    })

    expect(label(wrapper, 5)).toBe('10月5日 病假')
    expect(label(wrapper, 13)).toBe('10月13日 事假')
    // 單寫「其他」聽不出是請假
    expect(label(wrapper, 20)).toBe('10月20日 請假（其他）')
    expect(label(wrapper, 21)).toBe('10月21日 請假')
    // 請假格不分假別上色：同一種樣式
    expect(cell(wrapper, 5).classes()).toContain('att-day--leave')
    expect(cell(wrapper, 13).classes()).toContain('att-day--leave')
    expect(cell(wrapper, 20).classes()).toContain('att-day--leave')
  })

  it('AttendanceCalendar labels every status with its FRONTEND-005 label', () => {
    const wrapper = mountCalendar({
      today: '2026-10-22',
      days: [
        day('2026-10-02', { status: 'present' }),
        day('2026-10-06', { status: 'left' }),
        day('2026-10-08', { status: 'absent' }),
        day('2026-10-22', { status: 'expected' }),
        day('2026-10-26', { status: 'expected' }),
      ],
    })

    expect(label(wrapper, 2)).toBe(`10月2日 ${ATTENDANCE_STATUS_META.present.label}`)
    expect(label(wrapper, 6)).toBe(`10月6日 ${ATTENDANCE_STATUS_META.left.label}`)
    expect(label(wrapper, 8)).toBe(`10月8日 ${ATTENDANCE_STATUS_META.absent.label}`)
    expect(label(wrapper, 22)).toBe(`10月22日 ${ATTENDANCE_STATUS_META.expected.label}`)
    // 今天以後的 expected 仍是預計到班
    expect(label(wrapper, 26)).toBe('10月26日 預計到班')
  })

  it('AttendanceCalendar labels days without a record', () => {
    const wrapper = mountCalendar({
      days: [day('2026-10-23', { status: null }), day('2026-10-24', { status: null, is_service_day: true })],
    })

    expect(label(wrapper, 23)).toBe('10月23日 無紀錄')
    expect(label(wrapper, 24)).toBe('10月24日 無紀錄')
    // days 裡完全沒有的日期也是無紀錄
    expect(label(wrapper, 28)).toBe('10月28日 無紀錄')
    expect(cell(wrapper, 23).classes()).toContain('att-day--none')
    expect(cell(wrapper, 28).find('.m3-icon').exists()).toBe(false)
    expect(cell(wrapper, 28).get('.att-day__mark').text()).toBe('')
  })

  it('AttendanceCalendar draws status as colour class plus icon', () => {
    const wrapper = mountCalendar({
      today: '2026-10-22',
      days: [
        day('2026-10-02', { status: 'present' }),
        day('2026-10-06', { status: 'left' }),
        day('2026-10-05', { status: 'leave', leave_type: 'sick' }),
        day('2026-10-08', { status: 'absent' }),
        day('2026-10-15', { status: 'expected' }),
        day('2026-10-22', { status: 'expected' }),
        day('2026-10-09', { is_service_day: false }),
      ],
    })

    const expected: [number, string, string | null][] = [
      [2, 'att-day--present', 'check'],
      [6, 'att-day--left', 'done_all'],
      [5, 'att-day--leave', 'event_busy'],
      [8, 'att-day--absent', 'close'],
      [15, 'att-day--unrecorded', 'remove'],
      [22, 'att-day--expected', 'schedule'],
    ]
    for (const [dayNumber, kind, icon] of expected) {
      const c = cell(wrapper, dayNumber)
      expect(c.classes(), String(dayNumber)).toContain(kind)
      expect(c.get('.att-day__mark .m3-icon').text(), String(dayNumber)).toBe(icon)
      expect(c.get('.att-day__mark').attributes('aria-hidden'), String(dayNumber)).toBe('true')
    }
    // 休息：沒有圖示，標記是「休」字
    const closed = cell(wrapper, 9)
    expect(closed.find('.m3-icon').exists()).toBe(false)
    expect(closed.get('.att-day__mark').text()).toBe('休')
  })

  it('AttendanceCalendar maps each status to its container tokens', () => {
    const pairs: [string, string][] = [
      ['present', 'primary'],
      ['left', 'secondary'],
      ['leave', 'tertiary'],
      ['absent', 'error'],
      ['expected', 'warning'],
    ]
    for (const [kind, token] of pairs) {
      const style = rule(`.att-day--${kind}`)
      expect(style, kind).toContain(`background: var(--m3-${token}-container)`)
      expect(style, kind).toContain(`color: var(--m3-on-${token}-container)`)
    }
    expect(rule('.att-day--unrecorded')).toContain('background: var(--m3-surface-container-high)')
    expect(rule('.att-day--closed')).toContain('color: var(--m3-on-surface-variant)')
    expect(rule('.att-day--none')).toContain('color: var(--m3-on-surface)')
    // 休息與無紀錄沒有底色
    expect(rule('.att-day--closed')).not.toContain('background')
    expect(rule('.att-day--none')).not.toContain('background')
  })

  it('AttendanceCalendar closed day wins over any status and stays clickable', async () => {
    const wrapper = mountCalendar({
      days: [day('2026-10-10', { is_service_day: false, status: 'present' })],
    })

    expect(label(wrapper, 10)).toBe('10月10日 休息')
    expect(cell(wrapper, 10).classes()).toContain('att-day--closed')
    expect(cell(wrapper, 10).classes()).not.toContain('att-day--present')

    await cell(wrapper, 10).trigger('click')
    await cell(wrapper, 28).trigger('click')
    expect(wrapper.emitted('select')).toEqual([['2026-10-10'], ['2026-10-28']])
  })

  it('AttendanceCalendar weekday header starts on sunday and is hidden from readers', () => {
    const wrapper = mountCalendar()

    const header = wrapper.get('.att-cal__weekdays')
    expect(header.attributes('aria-hidden')).toBe('true')
    expect(header.findAll('span').map((el) => el.text())).toEqual(['日', '一', '二', '三', '四', '五', '六'])
    expect(header.findAll('span').every((el) => el.classes().includes('m3-label-medium'))).toBe(true)
  })

  it('AttendanceCalendar renders the right grid for any month', () => {
    // [month, 前導空格數, 當月天數]；涵蓋月初週日 / 週二 / 週五 / 週六、閏年二月、跨年
    const cases: [string, number, number][] = [
      ['2026-02', 0, 28],
      ['2026-09', 2, 30],
      ['2026-05', 5, 31],
      ['2026-08', 6, 31],
      ['2026-11', 0, 30],
      ['2026-12', 2, 31],
      ['2027-01', 5, 31],
      ['2028-02', 2, 29],
    ]
    for (const [month, pads, total] of cases) {
      const wrapper = mountCalendar({ month, today: '2026-10-22' })
      const children = gridChildren(wrapper)
      expect(children.filter((el) => el.tagName === 'SPAN'), month).toHaveLength(pads)
      expect(children.filter((el) => el.tagName === 'BUTTON'), month).toHaveLength(total)
      // 補格只在月初，之後全是 button
      expect(children.slice(0, pads).every((el) => el.tagName === 'SPAN'), month).toBe(true)
      expect(children.slice(pads).every((el) => el.tagName === 'BUTTON'), month).toBe(true)
      expect(children[pads]?.textContent, month).toBe('1')
      expect(children[children.length - 1]?.getAttribute('aria-label'), month).toMatch(
        new RegExp(`^${Number(month.slice(5))}月${total}日 `),
      )
    }
  })

  it('AttendanceCalendar date arithmetic ignores the local time zone', () => {
    for (const tz of ['Pacific/Kiritimati', 'Pacific/Pago_Pago', 'UTC']) {
      vi.stubEnv('TZ', tz)
      const wrapper = mountCalendar({
        month: '2026-10',
        today: '2026-10-01',
        days: [day('2026-10-01', { status: 'present' })],
      })
      const children = gridChildren(wrapper)
      expect(children.filter((el) => el.tagName === 'SPAN'), tz).toHaveLength(4)
      expect(children.filter((el) => el.tagName === 'BUTTON'), tz).toHaveLength(31)
      expect(label(wrapper, 1), tz).toBe('10月1日 已到班')
      expect(cell(wrapper, 1).attributes('aria-current'), tz).toBe('date')
    }
    // 原始碼只用 Date.UTC 與 UTC getter，不用 toISOString 取日期
    const script = source.slice(0, source.indexOf('<template>'))
    expect(script).toContain('Date.UTC')
    expect(script).not.toContain('toISOString')
  })

  it('AttendanceCalendar ignores days outside the month and keeps the grid whole', () => {
    const wrapper = mountCalendar({
      days: [
        day('2026-09-30', { status: 'absent' }),
        day('2026-11-01', { status: 'absent' }),
        day('2026-10-03', { status: 'present' }),
      ],
    })

    expect(wrapper.findAll('.att-cal__grid button')).toHaveLength(31)
    expect(wrapper.findAll('.att-day--absent')).toHaveLength(0)
    expect(label(wrapper, 3)).toBe('10月3日 已到班')
  })

  it('AttendanceCalendar renders an empty grid for a malformed month', () => {
    expect(mountCalendar({ month: '2026-10' }).findAll('.att-cal__grid button')).toHaveLength(31)

    for (const month of ['', 'abc', '2026-13', '2026-00', '2026-1']) {
      const wrapper = mountCalendar({ month })
      expect(wrapper.findAll('.att-cal__grid button'), month).toHaveLength(0)
      expect(wrapper.findAll('.att-cal__pad'), month).toHaveLength(0)
      // 框架（表頭與圖例）仍在，不會整個壞掉
      expect(wrapper.findAll('.att-cal__weekdays span'), month).toHaveLength(7)
      expect(wrapper.findAll('.att-legend__item'), month).toHaveLength(7)
    }
  })

  it('AttendanceCalendar judges unregistered by comparing with today across months', () => {
    const past = mountCalendar({ month: '2026-09', today: '2026-10-22', days: [day('2026-09-24', { status: 'expected' })] })
    expect(label(past, 24)).toBe('9月24日 未登記')

    const future = mountCalendar({ month: '2026-11', today: '2026-10-22', days: [day('2026-11-03', { status: 'expected' })] })
    expect(label(future, 3)).toBe('11月3日 預計到班')

    // today 在其他月份時，本月沒有 aria-current
    expect(past.findAll('[aria-current]')).toHaveLength(0)
  })

  it('AttendanceCalendar marks selected and today independently', async () => {
    const wrapper = mountCalendar({ today: '2026-10-22', selectedDate: '2026-10-22' })

    const both = cell(wrapper, 22)
    expect(both.classes()).toEqual(expect.arrayContaining(['is-today', 'is-selected']))
    expect(both.attributes('aria-current')).toBe('date')
    expect(both.attributes('aria-pressed')).toBe('true')

    await wrapper.setProps({ selectedDate: '2026-10-15' })
    expect(cell(wrapper, 22).classes()).toContain('is-today')
    expect(cell(wrapper, 22).classes()).not.toContain('is-selected')
    expect(cell(wrapper, 22).attributes('aria-pressed')).toBe('false')
    expect(cell(wrapper, 15).classes()).toContain('is-selected')
    expect(cell(wrapper, 15).attributes('aria-pressed')).toBe('true')

    await wrapper.setProps({ selectedDate: null })
    expect(wrapper.findAll('[aria-pressed="true"]')).toHaveLength(0)
    expect(wrapper.findAll('.is-selected')).toHaveLength(0)
  })

  it('AttendanceCalendar cells are native buttons in the natural tab order', () => {
    const wrapper = mountCalendar()
    const buttons = wrapper.findAll('.att-cal__grid button')

    expect(buttons).toHaveLength(31)
    expect(buttons.every((b) => b.attributes('type') === 'button')).toBe(true)
    expect(buttons.some((b) => b.attributes('tabindex') !== undefined)).toBe(false)
    expect(buttons.every((b) => !!b.attributes('aria-label'))).toBe(true)
    expect(buttons.every((b) => b.get('.att-day__num').classes().includes('m3-body-medium'))).toBe(true)
  })

  it('AttendanceCalendar legend lists seven kinds with the same swatches', () => {
    const wrapper = mountCalendar()

    const legend = wrapper.get('.att-legend')
    expect(legend.attributes('aria-label')).toBe('圖例')
    const items = legend.findAll('.att-legend__item')
    expect(items.map((el) => el.get('.att-legend__label').text())).toEqual([
      '已到班',
      '已離班',
      '請假',
      '缺席',
      '預計到班',
      '未登記',
      '休息',
    ])
    const swatchKinds = items.map((el) => el.get('.att-legend__swatch').classes().find((c) => c.startsWith('att-day--')))
    expect(swatchKinds).toEqual([
      'att-day--present',
      'att-day--left',
      'att-day--leave',
      'att-day--absent',
      'att-day--expected',
      'att-day--unrecorded',
      'att-day--closed',
    ])
    const icons = items.map((el) => el.find('.att-legend__swatch .m3-icon').exists() && el.get('.att-legend__swatch .m3-icon').text())
    expect(icons).toEqual(['check', 'done_all', 'event_busy', 'close', 'schedule', 'remove', false])
    expect(items[6]?.get('.att-legend__swatch').text()).toBe('休')
    expect(items.every((el) => el.get('.att-legend__swatch').attributes('aria-hidden') === 'true')).toBe(true)
    expect(legend.findAll('button')).toHaveLength(0)
  })

  it('AttendanceCalendar legend labels match FRONTEND-005 and the cell labels', () => {
    const wrapper = mountCalendar()

    const text = wrapper.get('.att-legend').text()
    for (const status of ['present', 'left', 'leave', 'absent', 'expected'] as const) {
      expect(text, status).toContain(ATTENDANCE_STATUS_META[status].label)
    }
  })

  it('AttendanceCalendar grid and cell style follow design decisions', () => {
    // 表頭與日期格同為 7 欄等寬、欄距列距 4px
    expect(source).toMatch(
      /\.att-cal__weekdays,\s*\.att-cal__grid \{[^}]*grid-template-columns: repeat\(7, minmax\(0, 1fr\)\);[^}]*gap: 4px/,
    )
    expect(rule('.att-cal__pad')).toMatch(/height:\s*52px/)

    const dayStyle = rule('.att-day')
    expect(dayStyle).toMatch(/height:\s*52px/)
    expect(dayStyle).toContain('border-radius: var(--m3-shape-small)')
    expect(dayStyle).toContain('font-variant-numeric: tabular-nums')
    expect(dayStyle).toMatch(/flex-direction:\s*column/)
    expect(rule('.att-day__mark')).toMatch(/height:\s*16px/)

    // 今天：2px primary 內框 + 粗體數字；選中：primary 實心 + on-primary；兩者並存再加一圈 on-primary
    expect(rule('.att-day.is-today')).toContain('box-shadow: inset 0 0 0 2px var(--m3-primary)')
    expect(rule('.att-day.is-today .att-day__num')).toMatch(/font-weight:\s*700/)
    expect(rule('.att-day.is-selected')).toContain('background: var(--m3-primary)')
    expect(rule('.att-day.is-selected')).toContain('color: var(--m3-on-primary)')
    const both = rule('.att-day.is-selected.is-today')
    expect(both).toContain('inset 0 0 0 2px var(--m3-primary)')
    expect(both).toContain('inset 0 0 0 4px var(--m3-on-primary)')

    // 鍵盤焦點：2px on-surface 外框，與今天的 primary 內框區分
    const focus = rule('.att-day:focus-visible')
    expect(focus).toContain('outline: 2px solid var(--m3-on-surface)')
    expect(focus).toContain('outline-offset: 1px')
  })

  it('AttendanceCalendar state layer follows the M3 rule', () => {
    expect(rule('.att-day::before')).toContain('background: currentcolor')
    expect(rule('.att-day:hover::before')).toContain('opacity: var(--m3-state-hover)')
    expect(rule('.att-day:active::before')).toContain('opacity: var(--m3-state-pressed)')
    expect(rule('.att-day:focus-visible::before')).toContain('opacity: var(--m3-state-focus)')
  })

  it('AttendanceCalendar legend style follows design decisions', () => {
    const legend = rule('.att-legend')
    expect(legend).toContain('flex-wrap: wrap')
    expect(legend).toContain('border-top: 1px solid var(--m3-outline-variant)')
    const swatch = rule('.att-legend__swatch')
    expect(swatch).toMatch(/width:\s*24px/)
    expect(swatch).toMatch(/height:\s*24px/)
    expect(swatch).toContain('border-radius: var(--m3-shape-extra-small)')
    // 沒有底色的休息 / 無紀錄色塊要有細框才看得到
    expect(source).toMatch(
      /\.att-legend__swatch\.att-day--closed,\s*\.att-legend__swatch\.att-day--none \{[^}]*box-shadow:\s*inset 0 0 0 1px var\(--m3-outline-variant\)/,
    )
  })

  it('AttendanceCalendar colours come from m3 tokens only', () => {
    const style = source.slice(source.indexOf('<style'))

    expect(style).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(style).toContain('var(--m3-')
  })
})
