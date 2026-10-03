// FRONTEND-006：上傳前的前端檢查與 FormData 組裝（後台與家長端共用）。
// 前端檢查只為即時回饋；後端 read_validated_upload（BACKEND-016）以檔頭判定，仍是權威。

export interface UploadRule {
  maxBytes: number
  mimeTypes: readonly string[]
  /** 用於錯誤訊息，例如 'JPG / PNG / WEBP' */
  label: string
}

const MiB = 1024 * 1024
const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

// file.type 不在清單時（例如部分瀏覽器的 .heic 為空字串）改以副檔名比對
const EXTENSIONS_BY_MIME: Readonly<Record<string, readonly string[]>> = {
  'image/jpeg': ['jpg', 'jpeg'],
  'image/png': ['png'],
  'image/webp': ['webp'],
  'image/heic': ['heic'],
  'application/pdf': ['pdf'],
  [XLSX_MIME]: ['xlsx'],
}

/** 學生照片（BACKEND-154） */
export const PHOTO_RULE: UploadRule = {
  maxBytes: 5 * MiB,
  mimeTypes: ['image/jpeg', 'image/png', 'image/webp'],
  label: 'JPG / PNG / WEBP',
}

/** 請假附件（BACKEND-358） */
export const ATTACHMENT_RULE: UploadRule = {
  maxBytes: 10 * MiB,
  mimeTypes: [...PHOTO_RULE.mimeTypes, 'image/heic', 'application/pdf'],
  label: 'JPG / PNG / WEBP / HEIC / PDF',
}

/** 學生 Excel 匯入（BACKEND-166） */
export const XLSX_RULE: UploadRule = {
  maxBytes: 5 * MiB,
  mimeTypes: [XLSX_MIME],
  label: 'Excel（.xlsx）',
}

export type UploadCheck =
  | { ok: true }
  | { ok: false; code: 'file_empty' | 'file_too_large' | 'unsupported_file_type'; message: string }

function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf('.')
  return dot < 0 ? '' : filename.slice(dot + 1).toLowerCase()
}

function isAllowedType(file: File, rule: UploadRule): boolean {
  if (rule.mimeTypes.includes(file.type)) return true
  const ext = extensionOf(file.name)
  return ext !== '' && rule.mimeTypes.some((mime) => EXTENSIONS_BY_MIME[mime]?.includes(ext))
}

export function checkUpload(file: File, rule: UploadRule): UploadCheck {
  if (file.size === 0) {
    return { ok: false, code: 'file_empty', message: '檔案是空的' }
  }
  if (file.size > rule.maxBytes) {
    return {
      ok: false,
      code: 'file_too_large',
      message: `檔案超過 ${Math.floor(rule.maxBytes / MiB)} MB`,
    }
  }
  if (!isAllowedType(file, rule)) {
    return { ok: false, code: 'unsupported_file_type', message: `只接受 ${rule.label}` }
  }
  return { ok: true }
}

export function buildFormData(
  fields: Record<string, string | number | boolean | Blob | null | undefined>,
): FormData {
  const fd = new FormData()
  for (const [key, value] of Object.entries(fields)) {
    if (value === null || value === undefined) continue
    if (value instanceof Blob) {
      // File 是 Blob 子類別，append 時保留原檔名
      fd.append(key, value)
    } else {
      fd.append(key, String(value))
    }
  }
  return fd
}
