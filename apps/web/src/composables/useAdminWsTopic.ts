// FRONTEND-038：頁面層訂閱 /api/ws/admin 的一個 topic（出勤 FRONTEND-126、作業看板 FRONTEND-152、
// 接送 FRONTEND-188 共用）。掛載時 subscribe、綁事件 handler、註冊斷線輪詢與重連補抓；卸載時全部解除。
// 訂閱由 adminWs store 以引用計數合併：多個元件共用同一 topic 只送一次 subscribe，全部卸載才 unsubscribe。
// resync 不併發：執行中再被觸發（輪詢、重連、頁面重新可見）就記為待補，本輪結束（成功或失敗）後立刻補跑一次，
// 多次觸發合併成一次。只略過會漏資料：t=0 斷線送出 R1、t=1000 重連成功時 R1 仍在跑，
// 若略過重連那次，R1 完成後畫面就停在 t=0 的快照，斷線期間遺失的廣播要等下一個事件才會補回。
import { computed, onMounted, onScopeDispose, type ComputedRef } from 'vue'
import type { WsStatus } from '@/shared/ws/createWsClient'
import { useAdminWsStore, type Topic } from '@/stores/adminWs'

// 各頁傳入的 handler 參數型別由頁面自己標註（例如 (row: AttendanceRow) => void），這裡不限制
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type WsEventHandler = (data: any) => void

export interface AdminWsTopicOptions {
  /** 斷線期間每次輪詢與重連成功後呼叫，重新抓一次頁面資料 */
  resync: () => void | Promise<void>
}

export interface AdminWsTopic {
  status: ComputedRef<WsStatus>
  /** topic 在 deniedTopics 中：頁面顯示「沒有即時更新權限」並只靠手動重新整理 */
  denied: ComputedRef<boolean>
}

export function useAdminWsTopic(
  topic: Topic,
  handlers: Record<string, WsEventHandler>,
  opts: AdminWsTopicOptions,
): AdminWsTopic {
  const adminWs = useAdminWsStore()
  const offs: (() => void)[] = []
  let subscribed = false
  let resyncing = false
  /** 本輪進行中又被觸發：結束後補跑一次（卸載時清掉，上一輪結束就不再補） */
  let rerunPending = false

  function finishResync(): void {
    resyncing = false
    if (!rerunPending) return
    rerunPending = false
    runResync()
  }

  function runResync(): void {
    if (resyncing) {
      rerunPending = true
      return
    }
    resyncing = true
    let result: void | Promise<void>
    try {
      result = opts.resync()
    } catch (err) {
      console.error('[useAdminWsTopic] resync 失敗', topic, err)
      finishResync()
      return
    }
    void Promise.resolve(result)
      .catch((err: unknown) => {
        console.error('[useAdminWsTopic] resync 失敗', topic, err)
      })
      .finally(finishResync)
  }

  onMounted(() => {
    adminWs.subscribe(topic)
    subscribed = true
    for (const [type, handler] of Object.entries(handlers)) offs.push(adminWs.on(type, handler))
    offs.push(adminWs.registerPoller(runResync), adminWs.onReconnect(runResync))
  })

  onScopeDispose(() => {
    rerunPending = false
    for (const off of offs.splice(0)) off()
    if (subscribed) {
      subscribed = false
      adminWs.unsubscribe(topic)
    }
  })

  return {
    status: computed(() => adminWs.status),
    denied: computed(() => adminWs.deniedTopics.includes(topic)),
  }
}
