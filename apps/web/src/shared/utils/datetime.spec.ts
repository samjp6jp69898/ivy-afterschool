import { describe, expect, it } from 'vitest'
import {
  addDays,
  compareHHMM,
  formatDate,
  formatDateTime,
  formatDateWithWeekday,
  formatTime,
  hhmmToUtcIso,
  isTodayTaipei,
  isValidHHMM,
  minutesSince,
  monthOf,
  nowHHMM,
  TAIPEI_TZ,
  todayTaipei,
  toTaipeiDate,
  utcIsoToHHMM,
  weekdayLabel,
} from './datetime'

describe('datetime', () => {
  it('datetime todayTaipei crosses midnight at UTC 16:00', () => {
    expect(TAIPEI_TZ).toBe('Asia/Taipei')
    expect(todayTaipei(new Date('2026-09-01T15:59:59Z'))).toBe('2026-09-01')
    expect(todayTaipei(new Date('2026-09-01T16:00:00Z'))).toBe('2026-09-02')
  })

  it('datetime isTodayTaipei', () => {
    const now = new Date('2026-10-02T03:00:00Z')

    expect(isTodayTaipei('2026-10-02', now)).toBe(true)
    expect(isTodayTaipei('2026-10-01', now)).toBe(false)
    // 台北 00:00（UTC 前一天 16:00）已是新的一天
    expect(isTodayTaipei('2026-10-02', new Date('2026-10-01T16:00:00Z'))).toBe(true)
    expect(isTodayTaipei('2026-10-02', new Date('2026-10-01T15:59:59Z'))).toBe(false)
  })

  it('datetime toTaipeiDate', () => {
    expect(toTaipeiDate('2026-09-30T17:30:00Z')).toBe('2026-10-01')
    expect(toTaipeiDate('2026-09-30T15:59:59Z')).toBe('2026-09-30')
  })

  it('datetime formatters', () => {
    expect(formatDate('2026-10-02')).toBe('2026/10/02')
    expect(formatDate('2026-09-30T17:30:00Z')).toBe('2026/10/01')
    expect(formatDate(null)).toBe('—')
    expect(formatDateWithWeekday('2026-10-02')).toBe('10/02（五）')
    expect(formatTime('2026-10-02T08:05:00Z')).toBe('16:05')
    expect(formatTime('2026-10-01T16:00:00Z')).toBe('00:00')
    expect(formatDateTime('2026-10-02T08:05:00Z')).toBe('2026/10/02 16:05')
    expect(formatDateTime(null)).toBe('—')
    expect(formatTime(null)).toBe('—')
    expect(formatTime('abc')).toBe('—')
    expect(formatDate('abc')).toBe('—')
  })

  it('datetime hhmm and utc conversion', () => {
    expect(hhmmToUtcIso('2026-10-02', '16:30')).toBe('2026-10-02T08:30:00.000Z')
    expect(utcIsoToHHMM('2026-10-02T08:30:00Z')).toBe('16:30')
    expect(hhmmToUtcIso('2026-10-02', '07:00')).toBe('2026-10-01T23:00:00.000Z')
    expect(() => hhmmToUtcIso('2026-10-02', '25:00')).toThrow(RangeError)
    expect(() => hhmmToUtcIso('2026-10-02', '25:00')).toThrow('invalid HH:MM')
    // 可逆
    expect(utcIsoToHHMM(hhmmToUtcIso('2026-10-02', '07:00'))).toBe('07:00')
    expect(toTaipeiDate(hhmmToUtcIso('2026-10-02', '07:00'))).toBe('2026-10-02')
    expect(nowHHMM(new Date('2026-10-02T09:41:59Z'))).toBe('17:41')
  })

  it('datetime hhmm validation and compare', () => {
    expect(isValidHHMM('09:05')).toBe(true)
    expect(isValidHHMM('23:59')).toBe(true)
    expect(isValidHHMM('9:05')).toBe(false)
    expect(isValidHHMM('24:00')).toBe(false)
    expect(isValidHHMM('12:60')).toBe(false)
    expect(compareHHMM('16:30', '17:00')).toBeLessThan(0)
    expect(compareHHMM('17:00', '17:00')).toBe(0)
    expect(compareHHMM('17:01', '17:00')).toBeGreaterThan(0)
  })

  it('datetime addDays and monthOf', () => {
    expect(addDays('2026-09-30', 1)).toBe('2026-10-01')
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28')
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01')
    expect(monthOf('2026-10-02')).toBe('2026-10')
    expect(weekdayLabel('2026-10-04')).toBe('日')
    expect(weekdayLabel('2026-10-05')).toBe('一')
  })

  it('datetime minutesSince', () => {
    expect(minutesSince('2026-10-02T08:00:00Z', new Date('2026-10-02T08:07:59Z'))).toBe(7)
    expect(minutesSince('2026-10-02T08:10:00Z', new Date('2026-10-02T08:07:59Z'))).toBe(0)
  })
})
