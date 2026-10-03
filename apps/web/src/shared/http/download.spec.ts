import type { AxiosResponse } from 'axios'
import { describe, expect, it, vi } from 'vitest'
import { filenameFromDisposition, saveBlob, saveBlobResponse } from './download'

function stubObjectUrl(url: string) {
  const create = vi.fn(() => url)
  const revoke = vi.fn()
  vi.spyOn(URL, 'createObjectURL').mockImplementation(create)
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(revoke)
  return { create, revoke }
}

function recordAnchorClicks() {
  const clicks: { download: string; href: string; attached: boolean }[] = []
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    clicks.push({ download: this.download, href: this.href, attached: document.body.contains(this) })
  })
  return clicks
}

function blobResponse(headers: Record<string, string>): AxiosResponse<Blob> {
  return { data: new Blob(['x']), headers } as unknown as AxiosResponse<Blob>
}

describe('download helpers', () => {
  it('download helpers parse content disposition', () => {
    expect(
      filenameFromDisposition(
        "attachment; filename*=UTF-8''%E5%87%BA%E5%8B%A4%E6%9C%88%E5%A0%B1_%E5%85%A8%E9%83%A8_2026-09.xlsx",
        'x.xlsx',
      ),
    ).toBe('出勤月報_全部_2026-09.xlsx')
    expect(filenameFromDisposition('attachment; filename="report.xlsx"', 'x.xlsx')).toBe('report.xlsx')
    expect(filenameFromDisposition(undefined, 'x.xlsx')).toBe('x.xlsx')
    expect(filenameFromDisposition("filename*=UTF-8''%E0%A4%A", 'x.xlsx')).toBe('x.xlsx')
  })

  it('download helpers prefer filename* and fall back to filename when it fails to decode', () => {
    expect(
      filenameFromDisposition(
        "attachment; filename=\"report.xlsx\"; filename*=UTF-8''%E5%87%BA%E5%8B%A4.xlsx",
        'x.xlsx',
      ),
    ).toBe('出勤.xlsx')
    expect(
      filenameFromDisposition("attachment; filename*=UTF-8''%E0%A4%A; filename=\"report.xlsx\"", 'x.xlsx'),
    ).toBe('report.xlsx')
    expect(filenameFromDisposition('attachment', 'x.xlsx')).toBe('x.xlsx')
  })

  it('download helpers saveBlob clicks a temporary anchor', () => {
    const { revoke } = stubObjectUrl('blob:1')
    const clicks = recordAnchorClicks()

    saveBlob(new Blob(['x']), '出勤.xlsx')

    expect(clicks).toEqual([{ download: '出勤.xlsx', href: 'blob:1', attached: true }])
    expect(document.querySelectorAll('a[download]').length).toBe(0)
    expect(revoke).toHaveBeenCalledWith('blob:1')
  })

  it('download helpers saveBlobResponse returns used filename', () => {
    stubObjectUrl('blob:2')
    const clicks = recordAnchorClicks()

    expect(saveBlobResponse(blobResponse({}), '出勤月報.xlsx')).toBe('出勤月報.xlsx')
    expect(
      saveBlobResponse(
        blobResponse({ 'content-disposition': "attachment; filename*=UTF-8''%E5%87%BA%E5%8B%A4.xlsx" }),
        '出勤月報.xlsx',
      ),
    ).toBe('出勤.xlsx')
    expect(clicks.map((c) => c.download)).toEqual(['出勤月報.xlsx', '出勤.xlsx'])
  })
})
