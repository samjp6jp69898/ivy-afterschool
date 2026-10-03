import { describe, expect, it } from 'vitest'
import { ATTACHMENT_RULE, buildFormData, checkUpload, PHOTO_RULE, XLSX_RULE } from './upload'

const MiB = 1024 * 1024
const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

function fileOfSize(bytes: number, name: string, type: string): File {
  return new File([new Uint8Array(bytes)], name, { type })
}

describe('upload helpers', () => {
  it('upload helpers checkUpload rejects empty, oversized and wrong type', () => {
    expect(checkUpload(new File([], 'a.jpg', { type: 'image/jpeg' }), PHOTO_RULE)).toEqual({
      ok: false,
      code: 'file_empty',
      message: '檔案是空的',
    })
    expect(checkUpload(fileOfSize(6 * MiB, 'a.png', 'image/png'), PHOTO_RULE)).toEqual({
      ok: false,
      code: 'file_too_large',
      message: '檔案超過 5 MB',
    })
    expect(checkUpload(fileOfSize(10, 'a.pdf', 'application/pdf'), PHOTO_RULE)).toEqual({
      ok: false,
      code: 'unsupported_file_type',
      message: '只接受 JPG / PNG / WEBP',
    })
  })

  it('upload helpers checkUpload accepts files within the size limit and listed type', () => {
    expect(checkUpload(fileOfSize(5 * MiB, 'a.webp', 'image/webp'), PHOTO_RULE)).toEqual({
      ok: true,
    })
    expect(checkUpload(fileOfSize(10 * MiB, 'a.pdf', 'application/pdf'), ATTACHMENT_RULE)).toEqual({
      ok: true,
    })
    expect(checkUpload(fileOfSize(10 * MiB + 1, 'a.pdf', 'application/pdf'), ATTACHMENT_RULE)).toEqual({
      ok: false,
      code: 'file_too_large',
      message: '檔案超過 10 MB',
    })
    expect(checkUpload(fileOfSize(10, 'students.xlsx', XLSX_MIME), XLSX_RULE)).toEqual({ ok: true })
  })

  it('upload helpers checkUpload accepts heic by extension', () => {
    const heic = new File([new Uint8Array([1, 2, 3])], 'IMG_0001.HEIC', { type: '' })

    expect(checkUpload(heic, ATTACHMENT_RULE)).toEqual({ ok: true })
    expect(checkUpload(heic, PHOTO_RULE)).toEqual({
      ok: false,
      code: 'unsupported_file_type',
      message: '只接受 JPG / PNG / WEBP',
    })
  })

  it('upload helpers checkUpload rejects empty type with no matching extension', () => {
    const noExt = new File([new Uint8Array([1])], 'photo', { type: '' })

    expect(checkUpload(noExt, ATTACHMENT_RULE)).toMatchObject({ ok: false, code: 'unsupported_file_type' })
  })

  it('upload helpers buildFormData', () => {
    const file = new File([new Uint8Array([1])], 'students.xlsx', { type: XLSX_MIME })

    const fd = buildFormData({ academic_year: 115, dry_run: true, note: null, file })

    expect(fd.get('academic_year')).toBe('115')
    expect(fd.get('dry_run')).toBe('true')
    expect(fd.has('note')).toBe(false)
    expect((fd.get('file') as File).name).toBe('students.xlsx')
  })

  it('upload helpers buildFormData skips undefined and stringifies false', () => {
    const fd = buildFormData({ dry_run: false, reason: undefined, title: '王小明' })

    expect(fd.get('dry_run')).toBe('false')
    expect(fd.has('reason')).toBe(false)
    expect(fd.get('title')).toBe('王小明')
  })
})
