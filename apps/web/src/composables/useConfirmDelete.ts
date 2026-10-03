// FRONTEND-044：刪除 / 封存前確認並處理結果訊息。移植 ivy FE:src/composables/useConfirmDelete.ts::useConfirmDelete 的
// 確認框文案與 deleting 狀態；改為注入型別化的 request（不再以 endpoint 字串拼 URL）。
// 「進行中」從確認框開啟算到請求結束，期間再呼叫直接回 false（防連點開出兩個確認框）；deleting 只反映請求期間。
import { ElMessage, ElMessageBox } from 'element-plus'
import { ref, type Ref } from 'vue'
import { errorMessage } from '@/shared/utils/errorMessage'

export interface ConfirmDeleteOptions<Row> {
  /** 回傳值即 result；api client 應回傳 response body */
  request: (row: Row) => Promise<unknown>
  confirmMessage: (row: Row) => string
  /** 預設「確認刪除」 */
  confirmTitle?: string
  /** 預設「刪除」 */
  confirmButtonText?: string
  /** 預設「已刪除」 */
  successMessage?: string | ((result: unknown, row: Row) => string)
  onSuccess?: (row: Row, result: unknown) => void
}

export function useConfirmDelete<Row>(opts: ConfirmDeleteOptions<Row>): {
  confirmDelete: (row: Row) => Promise<boolean>
  deleting: Ref<boolean>
} {
  const deleting = ref(false)
  let busy = false

  async function confirmDelete(row: Row): Promise<boolean> {
    if (busy) return false
    busy = true
    try {
      try {
        await ElMessageBox.confirm(opts.confirmMessage(row), opts.confirmTitle ?? '確認刪除', {
          type: 'warning',
          confirmButtonText: opts.confirmButtonText ?? '刪除',
          cancelButtonText: '取消',
        })
      } catch {
        return false
      }

      deleting.value = true
      let result: unknown
      try {
        result = await opts.request(row)
      } catch (err) {
        ElMessage.error(errorMessage(err, '刪除失敗'))
        return false
      } finally {
        deleting.value = false
      }

      const message =
        typeof opts.successMessage === 'function'
          ? opts.successMessage(result, row)
          : (opts.successMessage ?? '已刪除')
      ElMessage.success(message)
      opts.onSuccess?.(row, result)
      return true
    } finally {
      busy = false
    }
  }

  return { confirmDelete, deleting }
}
