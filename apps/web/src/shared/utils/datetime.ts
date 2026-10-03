// FRONTEND-003：Asia/Taipei 日期時間工具，前後台所有日期 / 時間顯示與換算都走這裡。
// 移植 ivy FE:src/utils/taipeiTime.ts（formatTaipeiClock、taipeiDayKey）與 FE:src/utils/format.ts::todayTaipeiISO
// 的 Intl.DateTimeFormat(timeZone: 'Asia/Taipei') 寫法；不依賴執行環境時區。
// 禁止 toISOString().slice(...) 取日期（UTC 會跨日）。台灣無日光節約，一律以固定 +08:00 換算。
import type { HHMM, ISODate, ISODateTime } from '@/shared/types/api'

export const TAIPEI_TZ = 'Asia/Taipei'

const EMPTY = '—'
const ISO_DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/
const HHMM_RE = /^([01]\d|2[0-3]):[0-5]\d$/
const WEEKDAY_LABELS = ['日', '一', '二', '三', '四', '五', '六'] as const

const taipeiParts = new Intl.DateTimeFormat('en-US', {
  timeZone: TAIPEI_TZ,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

interface TaipeiParts {
  year: string
  month: string
  day: string
  hour: string
  minute: string
}

function partsOf(d: Date): TaipeiParts {
  const out: Record<string, string> = {}
  for (const p of taipeiParts.formatToParts(d)) out[p.type] = p.value
  return {
    year: out.year ?? '',
    month: out.month ?? '',
    day: out.day ?? '',
    hour: out.hour ?? '',
    minute: out.minute ?? '',
  }
}

function parseDateTime(iso: string): Date | null {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d
}

/** 'YYYY-MM-DD' → 該日的 UTC 00:00（只做日曆運算，與時區無關）；格式或日期不合法回 null */
function parseIsoDate(date: string): Date | null {
  const m = ISO_DATE_RE.exec(date)
  if (!m) return null
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const utc = new Date(Date.UTC(y, mo - 1, d))
  // 拒絕 2026-02-30 這類會被 Date 自動進位的日期
  if (utc.getUTCFullYear() !== y || utc.getUTCMonth() !== mo - 1 || utc.getUTCDate() !== d) return null
  return utc
}

function requireIsoDate(date: string): Date {
  const d = parseIsoDate(date)
  if (!d) throw new RangeError('invalid date')
  return d
}

const pad2 = (n: number): string => String(n).padStart(2, '0')

function isoDateFromUtcCalendar(d: Date): ISODate {
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`
}

function taipeiDateOf(d: Date): ISODate {
  const p = partsOf(d)
  return `${p.year}-${p.month}-${p.day}`
}

function taipeiHHMMOf(d: Date): HHMM {
  const p = partsOf(d)
  return `${p.hour}:${p.minute}`
}

export function todayTaipei(now: Date = new Date()): ISODate {
  return taipeiDateOf(now)
}

export function isTodayTaipei(date: ISODate, now: Date = new Date()): boolean {
  return date === todayTaipei(now)
}

/** UTC 時間點落在台北哪一天 */
export function toTaipeiDate(iso: ISODateTime): ISODate {
  const d = parseDateTime(iso)
  if (!d) throw new RangeError('invalid datetime')
  return taipeiDateOf(d)
}

export function addDays(date: ISODate, days: number): ISODate {
  const d = requireIsoDate(date)
  d.setUTCDate(d.getUTCDate() + days)
  return isoDateFromUtcCalendar(d)
}

/** 'YYYY-MM' */
export function monthOf(date: ISODate): string {
  requireIsoDate(date)
  return date.slice(0, 7)
}

/** '一'~'日' */
export function weekdayLabel(date: ISODate): string {
  return WEEKDAY_LABELS[requireIsoDate(date).getUTCDay()] ?? ''
}

/** 純日期照原日顯示；時間點換算成台北日期。'2026/10/02'；null 或不合法 → '—' */
export function formatDate(date: ISODate | ISODateTime | null): string {
  if (date == null) return EMPTY
  if (ISO_DATE_RE.test(date)) {
    return parseIsoDate(date) ? date.replaceAll('-', '/') : EMPTY
  }
  const d = parseDateTime(date)
  return d ? taipeiDateOf(d).replaceAll('-', '/') : EMPTY
}

/** '10/02（五）'；不合法 → '—' */
export function formatDateWithWeekday(date: ISODate): string {
  const d = parseIsoDate(date)
  if (!d) return EMPTY
  return `${date.slice(5, 7)}/${date.slice(8, 10)}（${WEEKDAY_LABELS[d.getUTCDay()]}）`
}

/** 台北 'HH:mm'；null 或不合法 → '—' */
export function formatTime(iso: ISODateTime | null): string {
  if (iso == null) return EMPTY
  const d = parseDateTime(iso)
  return d ? taipeiHHMMOf(d) : EMPTY
}

/** '2026/10/02 16:05'；null 或不合法 → '—' */
export function formatDateTime(iso: ISODateTime | null): string {
  if (iso == null) return EMPTY
  const d = parseDateTime(iso)
  return d ? `${taipeiDateOf(d).replaceAll('-', '/')} ${taipeiHHMMOf(d)}` : EMPTY
}

export function isValidHHMM(v: string): boolean {
  return HHMM_RE.test(v)
}

/** 固定兩位數格式，字串比較即時間先後 */
export function compareHHMM(a: HHMM, b: HHMM): number {
  return a < b ? -1 : a > b ? 1 : 0
}

/** 台北當地日期 + 時間 → UTC ISO（'...Z'） */
export function hhmmToUtcIso(date: ISODate, hhmm: HHMM): ISODateTime {
  if (!isValidHHMM(hhmm)) throw new RangeError('invalid HH:MM')
  requireIsoDate(date)
  return new Date(`${date}T${hhmm}:00+08:00`).toISOString()
}

/** UTC 時間點 → 台北 'HH:MM' */
export function utcIsoToHHMM(iso: ISODateTime): HHMM {
  const d = parseDateTime(iso)
  if (!d) throw new RangeError('invalid datetime')
  return taipeiHHMMOf(d)
}

export function nowHHMM(now: Date = new Date()): HHMM {
  return taipeiHHMMOf(now)
}

/** 經過的整分鐘數（無條件捨去）；未來時間或不合法輸入回 0 */
export function minutesSince(iso: ISODateTime, now: Date = new Date()): number {
  const d = parseDateTime(iso)
  if (!d) return 0
  return Math.max(0, Math.floor((now.getTime() - d.getTime()) / 60_000))
}
