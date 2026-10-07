import { mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { ParentExamDetail } from '../../api/exams'
import ExamScoreTable from './ExamScoreTable.vue'
import source from './ExamScoreTable.vue?raw'

type Subject = ParentExamDetail['subjects'][number]

function subject(overrides: Partial<Subject> = {}): Subject {
  return { subject_name: '國語', full_score: 100, score: null, is_absent: false, note: null, ...overrides }
}

function mountTable(subjects: Subject[]) {
  return mount(ExamScoreTable, { props: { subjects } })
}

function row(wrapper: VueWrapper, index: number): DOMWrapper<Element> {
  const found = wrapper.findAll('tbody tr')[index]
  if (!found) throw new Error(`第 ${index + 1} 列不存在`)
  return found
}

/** ?raw 原始碼中某選擇器（行首，可縮排）的樣式區塊內容 */
function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = new RegExp(`^\\s*${escaped} \\{([^}]*)\\}`, 'm').exec(source)
  expect(match, `找不到 ${selector} 區塊`).not.toBeNull()
  return match?.[1] ?? ''
}

describe('ExamScoreTable', () => {
  it('ExamScoreTable renders scores and absent', () => {
    const wrapper = mountTable([
      subject({ subject_name: '國語', full_score: 100, score: 92.5, is_absent: false, note: null }),
      subject({ subject_name: '數學', full_score: 100, score: null, is_absent: true, note: '請病假' }),
    ])

    const first = row(wrapper, 0)
    expect(first.text()).toContain('國語')
    expect(first.text()).toContain('92.5')
    expect(first.text()).toContain('100')
    const second = row(wrapper, 1)
    expect(second.text()).toContain('數學')
    expect(second.text()).toContain('缺考')
    expect(second.text()).toContain('請病假')
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
  })

  it('ExamScoreTable integer and unrecorded', () => {
    const wrapper = mountTable([
      subject({ subject_name: '英語', score: 88 }),
      subject({ subject_name: '社會', score: null, is_absent: false }),
    ])

    expect(row(wrapper, 0).get('.score-table__score').text()).toBe('88')
    expect(row(wrapper, 0).text()).not.toContain('88.0')
    expect(row(wrapper, 1).get('.score-table__score').text()).toBe('未登記')
  })

  it('ExamScoreTable empty subjects', () => {
    const wrapper = mountTable([])

    expect(wrapper.text()).toContain('這次考試尚無科目成績')
    expect(wrapper.find('table').exists()).toBe(false)
  })

  it('ExamScoreTable is a semantic table with a hidden caption and three column headers', () => {
    const wrapper = mountTable([subject({ score: 90 })])

    const caption = wrapper.get('caption')
    expect(caption.text()).toBe('各科成績')
    expect(caption.classes()).toContain('visually-hidden')
    expect(rule('.visually-hidden')).toContain('position: absolute')
    const headers = wrapper.findAll('thead th')
    expect(headers.map((th) => th.text())).toEqual(['科目', '分數', '滿分'])
    expect(headers.map((th) => th.attributes('scope'))).toEqual(['col', 'col', 'col'])
    expect(headers.every((th) => th.classes().includes('m3-label-medium'))).toBe(true)
  })

  it('ExamScoreTable formats scores and full scores to at most one decimal', () => {
    const cases: [number, string][] = [
      [92.5, '92.5'],
      [101.5, '101.5'],
      [88, '88'],
      [100, '100'],
      [0, '0'],
      [87.04, '87'],
      [87.96, '88'],
      [66.66, '66.7'],
    ]
    for (const [score, text] of cases) {
      const wrapper = mountTable([subject({ score, full_score: 120 })])
      expect(row(wrapper, 0).get('.score-table__score').text(), String(score)).toBe(text)
    }

    const full = mountTable([
      subject({ subject_name: 'a', score: 40, full_score: 50 }),
      subject({ subject_name: 'b', score: 40, full_score: 99.5 }),
      subject({ subject_name: 'c', score: 40, full_score: 100.0 }),
    ])
    expect([0, 1, 2].map((i) => row(full, i).get('.score-table__full').text())).toEqual(['50', '99.5', '100'])
  })

  it('ExamScoreTable absent wins over a stored score and uses the error colour', () => {
    const wrapper = mountTable([subject({ score: 60, is_absent: true })])

    const cell = row(wrapper, 0).get('.score-table__score')
    expect(cell.text()).toBe('缺考')
    expect(cell.classes()).toContain('is-absent')
    expect(row(wrapper, 0).text()).not.toContain('60')
    expect(rule('.score-table__score.is-absent')).toContain('color: var(--m3-error)')
  })

  it('ExamScoreTable unrecorded is plain text with muted colour and normal weight', () => {
    const wrapper = mountTable([subject({ score: null, is_absent: false })])

    const cell = row(wrapper, 0).get('.score-table__score')
    expect(cell.text()).toBe('未登記')
    expect(cell.classes()).toContain('is-unrecorded')
    expect(cell.classes()).not.toContain('is-absent')
    const style = rule('.score-table__score.is-unrecorded')
    expect(style).toContain('color: var(--m3-on-surface-variant)')
    expect(style).toMatch(/font-weight:\s*400/)
    // 缺考 / 未登記只用文字表達，不另加圖示
    expect(wrapper.find('.m3-icon').exists()).toBe(false)
  })

  it('ExamScoreTable puts the note under the subject name in the same row', () => {
    const wrapper = mountTable([
      subject({ subject_name: '自然', score: 100, note: '實驗題全對\n表現很好！' }),
      subject({ subject_name: '社會', score: 80, note: null }),
    ])

    // 備註不另起一列：每科一列
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    const cell = row(wrapper, 0).get('td')
    expect(cell.get('.score-table__subject').text()).toBe('自然')
    expect(cell.get('.score-table__note').element.textContent).toBe('實驗題全對\n表現很好！')
    expect(cell.get('.score-table__note').classes()).toContain('m3-body-small')
    expect(cell.get('.score-table__subject').classes()).toContain('m3-body-large')
    expect(row(wrapper, 1).find('.score-table__note').exists()).toBe(false)

    const note = rule('.score-table__note')
    expect(note).toContain('white-space: pre-wrap')
    expect(note).toContain('color: var(--m3-on-surface-variant)')
    expect(note).toContain('overflow-wrap: anywhere')
  })

  it('ExamScoreTable shows only subject score and full score columns', () => {
    const wrapper = mountTable([
      subject({ subject_name: '國語', score: 92.5 }),
      subject({ subject_name: '數學', score: 70 }),
      subject({ subject_name: '英語', score: 88 }),
    ])

    for (const tr of wrapper.findAll('tbody tr')) {
      expect(tr.findAll('td')).toHaveLength(3)
    }
    expect(wrapper.findAll('thead th')).toHaveLength(3)
    expect(wrapper.text()).not.toMatch(/總分|平均|排名|名次|加總/)
    // 不依分數高低上色：分數格只可能帶缺考 / 未登記兩種狀態 class
    for (const cell of wrapper.findAll('.score-table__score')) {
      expect(cell.classes().filter((c) => c.startsWith('is-'))).toEqual([])
    }
  })

  it('ExamScoreTable keeps score and full score in numeric columns', () => {
    const wrapper = mountTable([subject({ score: 92.5 })])

    const cells = row(wrapper, 0).findAll('td')
    expect(cells[1]?.classes()).toContain('col-score')
    expect(cells[2]?.classes()).toContain('col-full')
    expect(cells[1]?.get('.score-table__score').classes()).toContain('m3-title-medium')
    expect(cells[2]?.get('.score-table__full').classes()).toContain('m3-body-medium')
    expect(rule('.score-table__score')).toContain('font-variant-numeric: tabular-nums')
    expect(rule('.score-table__full')).toContain('font-variant-numeric: tabular-nums')
    expect(rule('.score-table__full')).toContain('color: var(--m3-on-surface-variant)')
  })

  it('ExamScoreTable empty state is the shared empty state without description or action', () => {
    const wrapper = mountTable([])

    expect(wrapper.get('.empty-state__title').text()).toBe('這次考試尚無科目成績')
    expect(wrapper.get('.empty-state__icon').text()).toBe('grading')
    expect(wrapper.find('.empty-state__description').exists()).toBe(false)
    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('ExamScoreTable switches between table and empty state when subjects change', async () => {
    const wrapper = mountTable([])
    expect(wrapper.find('table').exists()).toBe(false)

    await wrapper.setProps({ subjects: [subject({ subject_name: '國語', score: 95 })] })
    expect(wrapper.find('table').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('這次考試尚無科目成績')
    expect(row(wrapper, 0).text()).toContain('國語')
  })

  it('ExamScoreTable layout follows design decisions', () => {
    const table = rule('.score-table')
    expect(table).toMatch(/width:\s*100%/)
    expect(table).toContain('table-layout: fixed')
    expect(table).toContain('border-collapse: collapse')

    const th = rule('.score-table th')
    expect(th).toMatch(/height:\s*40px/)
    expect(th).toMatch(/padding:\s*0 16px/)
    expect(th).toContain('color: var(--m3-on-surface-variant)')
    expect(th).toContain('border-bottom: 1px solid var(--m3-outline-variant)')
    expect(th).toContain('text-align: left')

    const td = rule('.score-table td')
    expect(td).toMatch(/padding:\s*12px 16px/)
    expect(td).toContain('vertical-align: top')
    expect(rule('.score-table tbody tr + tr td')).toContain('border-top: 1px solid var(--m3-outline-variant)')

    const score = rule('.score-table .col-score')
    expect(score).toMatch(/width:\s*84px/)
    expect(score).toContain('text-align: right')
    const full = rule('.score-table .col-full')
    expect(full).toMatch(/width:\s*72px/)
    expect(full).toContain('text-align: right')
  })

  it('ExamScoreTable colours come from m3 tokens only', () => {
    const style = source.slice(source.indexOf('<style'))

    expect(style).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(style).toContain('var(--m3-')
  })
})
