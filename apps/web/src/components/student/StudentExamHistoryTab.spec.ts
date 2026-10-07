import { flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import type { ExamHistoryItem } from '@/api/exams'
import { adminHttp } from '@/api/http'
import { createApiMock, mountWithApp } from '@/test/helpers'
import StudentExamHistoryTab from './StudentExamHistoryTab.vue'
import examHistorySource from './StudentExamHistoryTab.vue?raw'

const URL_S1 = '/admin/students/s1/exam-history'

const MIDTERM: ExamHistoryItem = {
  exam_id: 'e1',
  exam_name: '第一次段考',
  exam_type_name: '段考',
  exam_date: '2026-10-15',
  status: 'published',
  scores: [
    { subject_id: 'sub1', subject_name: '國語', score: 95, full_score: 100, is_absent: false },
    { subject_id: 'sub2', subject_name: '數學', score: null, full_score: 100, is_absent: true },
  ],
}

const QUIZ: ExamHistoryItem = {
  exam_id: 'e2',
  exam_name: '十月單元測驗',
  exam_type_name: '小考',
  exam_date: '2026-10-01',
  status: 'draft',
  scores: [
    { subject_id: 'sub1', subject_name: '國語', score: 88.5, full_score: 100, is_absent: false },
    { subject_id: 'sub2', subject_name: '數學', score: null, full_score: 100, is_absent: false },
  ],
}

let mock: MockAdapter

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
}

async function mountTab(studentId = 's1') {
  const mounted = await mountWithApp(StudentExamHistoryTab, { props: { studentId }, initialRoute: '/students' })
  await settle()
  return mounted
}

function cards(wrapper: VueWrapper) {
  return wrapper.findAll('[data-test=exam-history-item]')
}

function cardText(wrapper: VueWrapper, index: number): string {
  return cards(wrapper).at(index)?.text().replace(/\s+/g, ' ') ?? ''
}

/** 打開類型下拉，回傳 teleport 到 body 的選項（以 input 的 aria-controls 對應） */
async function typeOptions(wrapper: VueWrapper): Promise<HTMLElement[]> {
  const select = wrapper.find('[data-test=exam-type-filter]')
  if (!select.exists()) throw new Error('找不到考試類型篩選')
  await select.find('.el-select__wrapper').trigger('click')
  await settle()
  const listId = select.find('input').attributes('aria-controls')
  const list = listId ? document.getElementById(listId) : null
  if (!list) throw new Error('考試類型選單沒有出現')
  return Array.from(list.querySelectorAll<HTMLElement>('.el-select-dropdown__item'))
}

describe('StudentExamHistoryTab', () => {
  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    document.body.innerHTML = ''
  })

  it('StudentExamHistoryTab renders exams and scores', async () => {
    mock.onGet(URL_S1).reply(200, [MIDTERM])
    const { wrapper } = await mountTab()

    const text = wrapper.text()
    expect(text).toContain('2026/10/15')
    expect(text).toContain('第一次段考')
    expect(text).toContain('段考')
    expect(cardText(wrapper, 0)).toContain('國語 95 / 100')
    expect(cardText(wrapper, 0)).toContain('數學 缺考')
    expect(wrapper.find('.exam-hist__absent').text()).toBe('缺考')
    // 已發布的考試沒有「未發布」
    expect(text).not.toContain('未發布')
    expect(mock.history.get[0]?.url).toBe(URL_S1)
  })

  it('StudentExamHistoryTab marks drafts and missing scores', async () => {
    mock.onGet(URL_S1).reply(200, [QUIZ])
    const { wrapper } = await mountTab()

    const text = cardText(wrapper, 0)
    expect(text).toContain('未發布')
    expect(text).toContain('數學 未填')
    expect(text).toContain('國語 88.5 / 100')
    expect(wrapper.find('.exam-hist__missing').text()).toBe('未填')
    expect(wrapper.find('.el-tag').classes()).toContain('el-tag--info')
  })

  it('StudentExamHistoryTab navigates to exam', async () => {
    mock.onGet(URL_S1).reply(200, [MIDTERM])
    const { wrapper, router } = await mountTab()

    const link = wrapper.findAll('button').find((b) => b.text() === '第一次段考')
    await link?.trigger('click')
    await settle()

    expect(router.currentRoute.value.path).toBe('/exams/e1')
  })

  it('StudentExamHistoryTab empty state', async () => {
    mock.onGet(URL_S1).reply(200, [])
    const { wrapper } = await mountTab()

    expect(wrapper.text()).toContain('尚無考試紀錄')
    expect(cards(wrapper)).toHaveLength(0)
    // 沒有資料時不出現類型篩選
    expect(wrapper.find('[data-test=exam-type-filter]').exists()).toBe(false)
  })

  it('StudentExamHistoryTab shows a skeleton while loading', async () => {
    mock.onGet(URL_S1).reply(() => new Promise(() => {}))
    const { wrapper } = await mountTab()

    expect(wrapper.find('.el-skeleton').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('尚無考試紀錄')
  })

  it('StudentExamHistoryTab shows an error state and retries', async () => {
    mock.onGet(URL_S1).replyOnce(500, { error: { code: 'internal_error', message: '伺服器錯誤', details: null } })
    const { wrapper } = await mountTab()

    expect(wrapper.text()).toContain('無法載入成績紀錄')
    expect(wrapper.find('.empty-state').classes()).toContain('is-error')
    expect(cards(wrapper)).toHaveLength(0)

    mock.onGet(URL_S1).reply(200, [MIDTERM])
    await wrapper.find('[data-test=retry]').trigger('click')
    await settle()

    expect(mock.history.get).toHaveLength(2)
    expect(wrapper.text()).not.toContain('無法載入成績紀錄')
    expect(cardText(wrapper, 0)).toContain('第一次段考')
  })

  it('StudentExamHistoryTab orders exams by date descending', async () => {
    mock.onGet(URL_S1).reply(200, [QUIZ, MIDTERM])
    const { wrapper } = await mountTab()

    expect(cards(wrapper).map((c) => c.find('.exam-hist__date').text())).toEqual(['2026/10/15', '2026/10/01'])
  })

  it('StudentExamHistoryTab filters by exam type', async () => {
    mock.onGet(URL_S1).reply(200, [MIDTERM, QUIZ])
    const { wrapper } = await mountTab()
    expect(cards(wrapper)).toHaveLength(2)

    const options = await typeOptions(wrapper)
    expect(options.map((o) => o.textContent?.trim())).toEqual(['段考', '小考'])
    options.find((o) => o.textContent?.trim() === '小考')?.click()
    await settle()

    expect(cards(wrapper)).toHaveLength(1)
    expect(cardText(wrapper, 0)).toContain('十月單元測驗')
  })

  it('StudentExamHistoryTab reloads and resets the filter when the student changes', async () => {
    mock.onGet(URL_S1).reply(200, [MIDTERM, QUIZ])
    mock.onGet('/admin/students/s2/exam-history').reply(200, [
      { ...MIDTERM, exam_id: 'e9', exam_name: '期末考', exam_date: '2026-12-20' },
    ])
    const { wrapper } = await mountTab()
    const options = await typeOptions(wrapper)
    options.find((o) => o.textContent?.trim() === '小考')?.click()
    await settle()
    expect(cards(wrapper)).toHaveLength(1)

    await wrapper.setProps({ studentId: 's2' })
    await settle()

    expect(mock.history.get.map((c) => c.url)).toEqual([URL_S1, '/admin/students/s2/exam-history'])
    expect(cards(wrapper)).toHaveLength(1)
    expect(cardText(wrapper, 0)).toContain('期末考')
  })

  it('StudentExamHistoryTab ignores a stale response after switching students', async () => {
    let resolveFirst: (value: [number, ExamHistoryItem[]]) => void = () => {}
    mock.onGet(URL_S1).reply(() => new Promise((resolve) => (resolveFirst = resolve)))
    mock.onGet('/admin/students/s2/exam-history').reply(200, [])
    const { wrapper } = await mountTab()

    await wrapper.setProps({ studentId: 's2' })
    await settle()
    resolveFirst([200, [MIDTERM]])
    await settle()

    expect(cards(wrapper)).toHaveLength(0)
    expect(wrapper.text()).toContain('尚無考試紀錄')
  })

  it('StudentExamHistoryTab layout rules follow the approved mockup', () => {
    // happy-dom 不計算版面：卡片、缺考橘字、未填灰字以原始碼斷言
    const style = examHistorySource.slice(examHistorySource.indexOf('<style'))
    expect(style).toMatch(/\.exam-hist\s*\{[^}]*border:\s*1px solid var\(--el-border-color-lighter\)/)
    expect(style).toMatch(/\.exam-hist__absent\s*\{[^}]*color:\s*var\(--el-color-warning-dark-2\)/)
    expect(style).toMatch(/\.exam-hist__missing\s*\{[^}]*color:\s*var\(--el-text-color-placeholder\)/)
  })
})
