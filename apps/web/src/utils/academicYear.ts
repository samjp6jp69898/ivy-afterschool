// FRONTEND-084：民國學年度與年級文字。移植 ivy FE:src/utils/academic.ts::currentRocYear / buildSchoolYearOptions
// 的民國年換算；學年度以台北時間 8 月 1 日切換（domain_spec M3：每年 8 月升級），不分學期。
import { todayTaipei } from '@/shared/utils/datetime'

const GRADE_NUMERALS = ['一', '二', '三', '四', '五', '六'] as const

/** 台北日期 ≥ 8/1 → 西元年 − 1911；否則 − 1912 */
export function currentAcademicYear(now: Date = new Date()): number {
  const today = todayTaipei(now)
  const year = Number(today.slice(0, 4))
  const month = Number(today.slice(5, 7))
  return month >= 8 ? year - 1911 : year - 1912
}

/** label '115 學年度'，新到舊 */
export function academicYearOptions(
  center: number,
  before = 2,
  after = 1,
): { label: string; value: number }[] {
  const options: { label: string; value: number }[] = []
  for (let y = center + after; y >= center - before; y -= 1) {
    options.push({ label: `${y} 學年度`, value: y })
  }
  return options
}

function gradeNumeral(level: number): string | undefined {
  return Number.isInteger(level) ? GRADE_NUMERALS[level - 1] : undefined
}

/** 1 → '一年級' … 6 → '六年級'；範圍外回 `${level} 年級` */
export function gradeLabel(level: number): string {
  const numeral = gradeNumeral(level)
  return numeral ? `${numeral}年級` : `${level} 年級`
}

/** [1,2] → '一、二年級'；[] → '—'；自動排序去重 */
export function gradeLevelsLabel(levels: number[]): string {
  const sorted = [...new Set(levels)].sort((a, b) => a - b)
  if (sorted.length === 0) return '—'
  return `${sorted.map((l) => gradeNumeral(l) ?? String(l)).join('、')}年級`
}

export const GRADE_OPTIONS: { label: string; value: number }[] = GRADE_NUMERALS.map((_, i) => ({
  label: gradeLabel(i + 1),
  value: i + 1,
}))
