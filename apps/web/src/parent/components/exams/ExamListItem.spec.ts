import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { createRouter, createWebHashHistory } from 'vue-router'
import { mountWithApp } from '@/test/helpers'
import type { ParentExamSummary } from '../../api/exams'
import ExamListItem from './ExamListItem.vue'
import source from './ExamListItem.vue?raw'

function exam(overrides: Partial<ParentExamSummary> = {}): ParentExamSummary {
  return {
    exam_id: 'e1',
    name: '第一次段考',
    exam_type_name: '段考',
    exam_date: '2026-10-14',
    published_at: '2026-10-16T08:00:00Z',
    subject_count: 4,
    ...overrides,
  }
}

/** 家長端正式路由用 hash history：href 形如 #/exams/e1 */
function mountWithHashRouter(props: { exam: ParentExamSummary }) {
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: { render: () => null } }],
  })
  return mount(ExamListItem, { props, global: { plugins: [router] } })
}

/** ?raw 原始碼中某選擇器（行首，可縮排）的所有樣式區塊內容，依出現順序 */
function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = new RegExp(`^\\s*${escaped} \\{([^}]*)\\}`, 'm').exec(source)
  expect(match, `找不到 ${selector} 區塊`).not.toBeNull()
  return match?.[1] ?? ''
}

describe('ExamListItem', () => {
  it('ExamListItem renders summary and link', () => {
    const wrapper = mountWithHashRouter({ exam: exam() })

    expect(wrapper.text()).toContain('第一次段考')
    expect(wrapper.text()).toContain('段考')
    expect(wrapper.text()).toContain('2026/10/14（三）')
    expect(wrapper.text()).toContain('4 科')
    expect(wrapper.get('a').attributes('href')).toMatch(/#\/exams\/e1$/)

    expect(wrapper.get('.exam-item__name').text()).toBe('第一次段考')
    expect(wrapper.get('.exam-item__type').text()).toBe('段考')
  })

  it('ExamListItem navigates on click', async () => {
    const { wrapper, router } = await mountWithApp(ExamListItem, { props: { exam: exam() } })
    expect(router.currentRoute.value.path).toBe('/')

    await wrapper.get('a').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/exams/e1')
  })

  it('ExamListItem is a list item holding a single link as its only interactive element', async () => {
    const { wrapper } = await mountWithApp(ExamListItem, { props: { exam: exam() } })

    expect(wrapper.element.tagName).toBe('LI')
    expect(wrapper.findAll('a')).toHaveLength(1)
    expect(wrapper.find('button, input, [role="button"]').exists()).toBe(false)
    expect(wrapper.get('li > a').text()).toContain('第一次段考')
  })

  it('ExamListItem shows weekday for any date and subject count', () => {
    const cases: [string, string, number, string][] = [
      ['2026-10-11', '2026/10/11（日）', 1, '1 科'],
      ['2026-10-17', '2026/10/17（六）', 6, '6 科'],
      ['2026-01-01', '2026/01/01（四）', 0, '0 科'],
    ]
    for (const [date, text, count, subjects] of cases) {
      const wrapper = mountWithHashRouter({ exam: exam({ exam_date: date, subject_count: count }) })
      expect(wrapper.get('.exam-item__meta').text(), date).toContain(text)
      expect(wrapper.get('.exam-item__meta').text(), date).toContain(subjects)
    }
  })

  it('ExamListItem meta line is type tag then date then subject count with a hidden dot', () => {
    const wrapper = mountWithHashRouter({ exam: exam() })
    const meta = wrapper.get('.exam-item__meta')

    expect(meta.classes()).toContain('m3-body-medium')
    expect(Array.from(meta.element.children).map((el) => el.textContent)).toEqual([
      '段考',
      '2026/10/14（三）',
      '·',
      '4 科',
    ])
    expect(meta.get('.exam-item__dot').attributes('aria-hidden')).toBe('true')
    expect(meta.get('.exam-item__type').classes()).toContain('m3-label-medium')
    expect(wrapper.get('.exam-item__name').classes()).toContain('m3-title-medium')
    expect(rule('.exam-item__meta')).toContain('color: var(--m3-on-surface-variant)')
    expect(rule('.exam-item__meta')).toContain('font-variant-numeric: tabular-nums')
  })

  it('ExamListItem shows a chevron and no score rank or publish time', () => {
    const wrapper = mountWithHashRouter({ exam: exam() })

    const chevron = wrapper.get('.exam-item__chevron')
    expect(chevron.text()).toBe('chevron_right')
    expect(chevron.attributes('aria-hidden')).toBe('true')
    expect(wrapper.get('a').element.lastElementChild).toBe(chevron.element)

    // 列表只做索引：不顯示分數 / 平均 / 名次，也不顯示公布時間
    expect(wrapper.text()).not.toContain('2026/10/16')
    expect(wrapper.text()).not.toContain('10/16')
    expect(wrapper.text()).not.toMatch(/分|名次|排名|平均/)
  })

  it('ExamListItem keeps the whole long name in the dom and clamps it to two lines', () => {
    const long = '115 學年度第一學期第一次定期評量（期中考）暨國語文閱讀理解能力檢測加考'
    const wrapper = mountWithHashRouter({ exam: exam({ name: long, exam_type_name: '全民英檢初級模擬測驗' }) })

    // 螢幕閱讀器仍讀全文：截斷只靠 CSS
    expect(wrapper.get('.exam-item__name').text()).toBe(long)
    expect(wrapper.get('.exam-item__type').text()).toBe('全民英檢初級模擬測驗')
    const name = rule('.exam-item__name')
    expect(name).toContain('-webkit-line-clamp: 2')
    expect(name).toContain('overflow: hidden')
    expect(name).toContain('overflow-wrap: anywhere')
    const type = rule('.exam-item__type')
    expect(type).toContain('max-width: 96px')
    expect(type).toContain('overflow: hidden')
    expect(type).toContain('text-overflow: ellipsis')
    expect(type).toContain('white-space: nowrap')
  })

  it('ExamListItem type tag uses tertiary container colours', () => {
    const type = rule('.exam-item__type')

    expect(type).toContain('background: var(--m3-tertiary-container)')
    expect(type).toContain('color: var(--m3-on-tertiary-container)')
    expect(type).toMatch(/height:\s*24px/)
    expect(type).toContain('border-radius: var(--m3-shape-extra-small)')
    expect(type).toMatch(/padding:\s*0 8px/)
  })

  it('ExamListItem row layout follows design decisions', () => {
    const link = rule('.exam-item__link')
    expect(link).toMatch(/min-height:\s*72px/)
    expect(link).toMatch(/padding:\s*12px 8px 12px 16px/)
    expect(link).toContain('text-decoration: none')
    expect(rule('.exam-item + .exam-item')).toContain('border-top: 1px solid var(--m3-outline-variant)')
    expect(rule('.exam-item__chevron')).toContain('color: var(--m3-on-surface-variant)')
  })

  it('ExamListItem state layer and focus ring match M3ListItem', () => {
    expect(rule('.exam-item__link::before')).toContain('background: var(--m3-on-surface)')
    expect(rule('.exam-item__link:hover::before')).toContain('opacity: var(--m3-state-hover)')
    expect(rule('.exam-item__link:active::before')).toContain('opacity: var(--m3-state-pressed)')
    expect(rule('.exam-item__link:focus-visible::before')).toContain('opacity: var(--m3-state-focus)')
    const focus = rule('.exam-item__link:focus-visible')
    expect(focus).toContain('outline: 2px solid var(--m3-primary)')
    expect(focus).toContain('outline-offset: -2px')
  })

  it('ExamListItem encodes the exam id into one path segment', () => {
    const wrapper = mountWithHashRouter({ exam: exam({ exam_id: 'a/b?c' }) })

    expect(wrapper.get('a').attributes('href')).toMatch(/#\/exams\/a%2Fb%3Fc$/)
  })

  it('ExamListItem colours come from m3 tokens only', () => {
    const style = source.slice(source.indexOf('<style'))

    expect(style).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(style).toContain('var(--m3-')
  })
})
