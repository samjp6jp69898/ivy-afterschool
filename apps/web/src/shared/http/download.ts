// FRONTEND-007：blob 回應存檔。移植 ivy FE:src/utils/download.ts::saveBlobResponse 的檔名解析；
// 不含錯誤提示（shared 不得依賴 element-plus，錯誤由呼叫端呈現）。
import type { AxiosResponse } from 'axios'

const FILENAME_STAR = /filename\*\s*=\s*UTF-8''([^;]+)/i
const FILENAME_PLAIN = /filename\s*=\s*(?:"([^"]*)"|([^;]+))/i

function decodeFilenameStar(header: string): string | null {
  const encoded = header.match(FILENAME_STAR)?.[1]?.trim()
  if (!encoded) return null
  try {
    return decodeURIComponent(encoded) || null
  } catch {
    return null
  }
}

function plainFilename(header: string): string | null {
  const m = header.match(FILENAME_PLAIN)
  return (m?.[1] ?? m?.[2])?.trim() || null
}

/** `filename*=UTF-8''...` 優先；解碼失敗改用 `filename="..."`；都取不到回 fallback。 */
export function filenameFromDisposition(header: string | undefined, fallback: string): string {
  if (!header) return fallback
  return decodeFilenameStar(header) ?? plainFilename(header) ?? fallback
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  try {
    link.click()
  } finally {
    link.remove()
    URL.revokeObjectURL(url)
  }
}

/** 依 Content-Disposition 決定檔名並存檔，回傳實際使用的檔名。 */
export function saveBlobResponse(res: AxiosResponse<Blob>, fallback: string): string {
  const header = res.headers['content-disposition'] as string | undefined
  const filename = filenameFromDisposition(header, fallback)
  saveBlob(res.data, filename)
  return filename
}
