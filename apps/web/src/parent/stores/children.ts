// PARENT-010：家長端小孩清單與目前選中的小孩。
// 合併 ivy 的 stores/children（requestVersion 丟棄過期回應）與 composables/useChildSelection（localStorage 記住選擇）。
// 登入 / 綁定回應已含小孩清單時用 seed 省一次請求；登出時 clear，避免下一位家長看到舊的小孩 id。
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { listChildren, type ChildSummary } from '../api/children'

const STORAGE_KEY = 'parent_selected_child_v1'
const LOAD_ERROR = '小孩清單載入失敗'

function readStoredId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY) || null
  } catch {
    // 無痕模式等情況 storage 會拋錯：只是少了跨 session 的記憶
    return null
  }
}

function writeStoredId(id: string | null): void {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id)
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // 同 readStoredId
  }
}

export const useChildrenStore = defineStore('parentChildren', () => {
  const items = ref<ChildSummary[]>([])
  const loaded = ref(false)
  const loading = ref(false)
  const error = ref('')
  const selectedId = ref<string | null>(readStoredId())

  // 每次 load / seed / clear 遞增；較舊請求的回應（版本不符）一律丟棄
  let requestVersion = 0
  let pending: Promise<void> | null = null

  const selectedChild = computed(() => items.value.find((c) => c.id === selectedId.value) ?? null)
  const hasMultiple = computed(() => items.value.length > 1)

  function setSelected(id: string | null): void {
    selectedId.value = id
    writeStoredId(id)
  }

  /** selectedId 仍在清單中就保留，否則選第一位；清單為空設 null */
  function ensureSelected(): void {
    if (selectedId.value !== null && items.value.some((c) => c.id === selectedId.value)) return
    setSelected(items.value[0]?.id ?? null)
  }

  /** 已載入且非 force 不重抓；載入中的非 force 呼叫共用同一個請求；force 會取代進行中的請求 */
  function load(force = false): Promise<void> {
    if (!force) {
      if (loaded.value) return Promise.resolve()
      if (pending) return pending
    }
    const version = ++requestVersion
    loading.value = true
    error.value = ''
    const request: Promise<void> = listChildren()
      .then(
        (children) => {
          if (version !== requestVersion) return
          items.value = children
          loaded.value = true
          ensureSelected()
        },
        () => {
          if (version !== requestVersion) return
          // 不清空既有 items
          error.value = LOAD_ERROR
        },
      )
      .finally(() => {
        if (version !== requestVersion) return
        loading.value = false
        pending = null
      })
    pending = request
    return request
  }

  /** 以登入 / 綁定回應的 ParentMe.children 直接填入，並丟棄進行中的載入 */
  function seed(children: ChildSummary[]): void {
    requestVersion += 1
    pending = null
    loading.value = false
    error.value = ''
    items.value = [...children]
    loaded.value = true
    ensureSelected()
  }

  function select(id: string): void {
    if (!items.value.some((c) => c.id === id)) return
    setSelected(id)
  }

  function clear(): void {
    requestVersion += 1
    pending = null
    items.value = []
    loaded.value = false
    loading.value = false
    error.value = ''
    setSelected(null)
  }

  return {
    items,
    loaded,
    loading,
    error,
    selectedId,
    selectedChild,
    hasMultiple,
    load,
    seed,
    select,
    ensureSelected,
    clear,
  }
})
