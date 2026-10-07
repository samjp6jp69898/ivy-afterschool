// FRONTEND-155 / 156：作業內容的「最近使用」，HomeworkItemDialog 與 HomeworkBatchAddDialog 共用。
// localStorage homework.recentTitles 存 JSON 字串陣列，最新在前、最多 5 筆。內容來自瀏覽器儲存、不可信：
// 讀取時只留去頭尾空白後 1~100 字的字串並去重；任何存取失敗（含取得 localStorage 本身丟 SecurityError）都不拋。

const STORAGE_KEY = 'homework.recentTitles'
const LIMIT = 5
/** 與後端作業項目 title 欄位上限一致 */
const MAX_LENGTH = 100

function normalize(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const title = value.trim()
  return title && title.length <= MAX_LENGTH ? title : null
}

export function loadRecentTitles(): string[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')
    if (!Array.isArray(parsed)) return []
    const titles = parsed.map(normalize).filter((t): t is string => t !== null)
    return [...new Set(titles)].slice(0, LIMIT)
  } catch {
    return []
  }
}

/** 移到最前、最多 5 筆；空白或超過 100 字不記錄 */
export function rememberRecentTitle(title: string): void {
  const normalized = normalize(title)
  if (normalized === null) return
  try {
    const next = [normalized, ...loadRecentTitles().filter((t) => t !== normalized)].slice(0, LIMIT)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    // 無痕模式、封鎖網站資料或儲存空間已滿：不記錄
  }
}
