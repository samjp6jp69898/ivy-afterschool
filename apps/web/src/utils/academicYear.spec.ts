import { describe, expect, it } from 'vitest'
import {
  academicYearOptions,
  currentAcademicYear,
  GRADE_OPTIONS,
  gradeLabel,
  gradeLevelsLabel,
} from './academicYear'

describe('academicYear', () => {
  it('academicYear switches on August 1 Taipei time', () => {
    expect(currentAcademicYear(new Date('2026-07-31T15:59:00Z'))).toBe(114)
    expect(currentAcademicYear(new Date('2026-07-31T16:00:00Z'))).toBe(115)
    expect(currentAcademicYear(new Date('2027-01-15T00:00:00Z'))).toBe(115)
    // 台北元旦（UTC 前一天 16:00）仍屬同一學年度
    expect(currentAcademicYear(new Date('2026-12-31T16:00:00Z'))).toBe(115)
    expect(currentAcademicYear(new Date('2027-07-31T15:59:59Z'))).toBe(115)
  })

  it('academicYear options newest first', () => {
    expect(academicYearOptions(115)).toEqual([
      { label: '116 學年度', value: 116 },
      { label: '115 學年度', value: 115 },
      { label: '114 學年度', value: 114 },
      { label: '113 學年度', value: 113 },
    ])
    expect(academicYearOptions(115, 0, 0)).toEqual([{ label: '115 學年度', value: 115 }])
  })

  it('academicYear grade labels', () => {
    expect(gradeLabel(3)).toBe('三年級')
    expect(gradeLabel(1)).toBe('一年級')
    expect(gradeLabel(6)).toBe('六年級')
    expect(gradeLabel(7)).toBe('7 年級')
    expect(gradeLabel(0)).toBe('0 年級')
    expect(gradeLevelsLabel([2, 1, 2])).toBe('一、二年級')
    expect(gradeLevelsLabel([6])).toBe('六年級')
    expect(gradeLevelsLabel([])).toBe('—')
    expect(GRADE_OPTIONS.length).toBe(6)
    expect(GRADE_OPTIONS[0]).toEqual({ label: '一年級', value: 1 })
    expect(GRADE_OPTIONS[5]).toEqual({ label: '六年級', value: 6 })
  })
})
