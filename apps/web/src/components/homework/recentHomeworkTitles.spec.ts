import { describe, expect, it, vi } from 'vitest'
import { loadRecentTitles, rememberRecentTitle } from './recentHomeworkTitles'

const KEY = 'homework.recentTitles'
const FIVE = ['國語第 5 課生字', '圈詞', '英語單字抄寫', '數學圈圈看', '自然習作 p.8']

function stored(): unknown {
  return JSON.parse(localStorage.getItem(KEY) ?? 'null')
}

function denied(): never {
  throw new DOMException('The operation is insecure.', 'SecurityError')
}

describe('recentHomeworkTitles', () => {
  it('recentHomeworkTitles reads valid titles only', () => {
    expect(loadRecentTitles()).toEqual([])

    localStorage.setItem(KEY, '{not json')
    expect(loadRecentTitles()).toEqual([])
    localStorage.setItem(KEY, '{"0":"圈詞"}')
    expect(loadRecentTitles()).toEqual([])
    localStorage.setItem(KEY, '"圈詞"')
    expect(loadRecentTitles()).toEqual([])

    localStorage.setItem(
      KEY,
      JSON.stringify([
        '國語第 5 課生字',
        3,
        '   ',
        '寫'.repeat(101),
        ' 圈詞 ',
        null,
        '國語第 5 課生字',
        '圈詞',
        '英語單字抄寫',
        '數學圈圈看',
        '自然習作 p.8',
        '社會習作',
      ]),
    )
    expect(loadRecentTitles()).toEqual(FIVE)

    // 剛好 100 字（title 欄位上限）保留
    localStorage.setItem(KEY, JSON.stringify(['寫'.repeat(100)]))
    expect(loadRecentTitles()).toEqual(['寫'.repeat(100)])
  })

  it('recentHomeworkTitles remembers trimmed title first', () => {
    localStorage.setItem(KEY, JSON.stringify(FIVE))

    rememberRecentTitle(' 圈詞 ')
    expect(stored()).toEqual(['圈詞', '國語第 5 課生字', '英語單字抄寫', '數學圈圈看', '自然習作 p.8'])

    rememberRecentTitle('數學習作 p.12-13')
    expect(stored()).toEqual(['數學習作 p.12-13', '圈詞', '國語第 5 課生字', '英語單字抄寫', '數學圈圈看'])

    rememberRecentTitle('   ')
    rememberRecentTitle('寫'.repeat(101))
    expect(stored()).toEqual(['數學習作 p.12-13', '圈詞', '國語第 5 課生字', '英語單字抄寫', '數學圈圈看'])
    expect(loadRecentTitles()).toEqual(stored())
  })

  it('recentHomeworkTitles starts over when storage is empty or corrupt', () => {
    rememberRecentTitle('圈詞')
    expect(stored()).toEqual(['圈詞'])

    localStorage.setItem(KEY, '{not json')
    rememberRecentTitle('數學圈圈看')
    expect(stored()).toEqual(['數學圈圈看'])
  })

  it('recentHomeworkTitles ignores storage failures', () => {
    vi.stubGlobal('localStorage', { getItem: denied, setItem: denied, removeItem: denied })
    expect(loadRecentTitles()).toEqual([])
    expect(() => rememberRecentTitle('圈詞')).not.toThrow()

    // 取得 localStorage 本身就丟（瀏覽器封鎖網站資料）；harness 的 afterEach 會還原 global
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, get: denied })
    expect(loadRecentTitles()).toEqual([])
    expect(() => rememberRecentTitle('圈詞')).not.toThrow()
  })

  it('recentHomeworkTitles keeps reading when storage is full', () => {
    localStorage.setItem(KEY, JSON.stringify(['圈詞']))
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
      throw new DOMException('Quota exceeded', 'QuotaExceededError')
    })

    expect(() => rememberRecentTitle('數學圈圈看')).not.toThrow()
    expect(loadRecentTitles()).toEqual(['圈詞'])
  })
})
