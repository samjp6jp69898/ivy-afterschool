import axios from 'axios'
import { describe, expect, it } from 'vitest'
import { ApiError } from '@/shared/types/api'
import { errorCode, errorMessage, isCanceled, validationFieldErrors } from './errorMessage'

describe('errorMessage', () => {
  it('errorMessage uses backend message or fallback', () => {
    expect(errorMessage(new ApiError(409, 'leave_overlap', '請假期間重疊'), '儲存失敗')).toBe('請假期間重疊')
    expect(errorMessage(new ApiError(500, 'x', ''), '儲存失敗')).toBe('儲存失敗')
    expect(errorMessage(new Error('boom'), '儲存失敗')).toBe('儲存失敗')
    expect(errorMessage({ message: '假的錯誤' }, '儲存失敗')).toBe('儲存失敗')
    expect(errorMessage(undefined, '儲存失敗')).toBe('儲存失敗')
  })

  it('errorMessage errorCode and isCanceled', () => {
    expect(errorCode(new ApiError(404, 'student_not_found', 'x'))).toBe('student_not_found')
    expect(errorCode('str')).toBeNull()
    expect(errorCode(new Error('x'))).toBeNull()
    expect(isCanceled(new axios.CanceledError())).toBe(true)
    expect(isCanceled(new Error())).toBe(false)
    expect(isCanceled(null)).toBe(false)
  })

  it('errorMessage maps validation details to fields', () => {
    const err = new ApiError(422, 'validation_error', '輸入有誤', [
      { loc: ['body', 'name'], msg: '必填', type: 'missing' },
      { loc: ['body', 'name'], msg: '過長', type: 'x' },
      { loc: ['body', 'guardians', 0, 'phone'], msg: '格式錯誤', type: 'x' },
    ])

    expect(validationFieldErrors(err)).toEqual({ name: '必填', 'guardians.0.phone': '格式錯誤' })
    expect(
      validationFieldErrors(
        new ApiError(409, 'leave_overlap', '重疊', [{ loc: ['body', 'name'], msg: '必填', type: 'x' }]),
      ),
    ).toEqual({})
    expect(validationFieldErrors(new Error('x'))).toEqual({})
  })

  it('errorMessage validation strips query prefix and tolerates malformed details', () => {
    const err = new ApiError(422, 'validation_error', '輸入有誤', [
      { loc: ['query', 'page_size'], msg: '不可超過 200', type: 'x' },
      { loc: ['path', 'student_id'], msg: '格式錯誤', type: 'x' },
      { msg: '缺 loc', type: 'x' },
      'not-an-item',
    ])

    expect(validationFieldErrors(err)).toEqual({ page_size: '不可超過 200', 'path.student_id': '格式錯誤' })
    expect(validationFieldErrors(new ApiError(422, 'validation_error', '輸入有誤', null))).toEqual({})
  })
})
