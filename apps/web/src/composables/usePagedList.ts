// FRONTEND-045：分頁列表狀態（學生、請假、考試、員工、稽核等共用）。
// 移植 ivy FE:src/composables/useTableFilters.ts::useTableFilters 的 page / filters 與序號防護；
// 搜尋框的 debounce 由使用端處理後呼叫 setFilters。
// 每次請求帶遞增序號，只有最新一次請求可以改 items / total / error / loading（慢回應不覆蓋快回應）。
import { reactive, ref, shallowRef, type Ref } from 'vue'
import { useRoute, useRouter, type LocationQuery, type LocationQueryRaw } from 'vue-router'
import type { Page } from '@/shared/types/api'
import { errorMessage } from '@/shared/utils/errorMessage'

export interface PagedListOptions<T, F extends Record<string, unknown>> {
  fetch: (params: F & { page: number; page_size: number }) => Promise<Page<T>>
  initialFilters: F
  /** 預設 20 */
  pageSize?: number
  /** true：page 與 filters 同步到 route.query（預設 false） */
  syncQuery?: boolean
  /** 預設 true */
  immediate?: boolean
}

export interface PagedList<T, F extends Record<string, unknown>> {
  items: Ref<T[]>
  total: Ref<number>
  page: Ref<number>
  pageSize: Ref<number>
  filters: F
  loading: Ref<boolean>
  error: Ref<string | null>
  reload(): Promise<void>
  setPage(n: number): void
  setFilters(patch: Partial<F>): void
}

function isEmptyValue(v: unknown): boolean {
  return v === undefined || v === null || v === ''
}

function cleanFilters(filters: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(filters)) {
    if (!isEmptyValue(v)) out[k] = v
  }
  return out
}

function firstQueryValue(v: LocationQuery[string] | undefined): string | null {
  const value = Array.isArray(v) ? v[0] : v
  return typeof value === 'string' ? value : null
}

/** 依 initialFilters 的型別轉換 query 字串；無法轉換時回 undefined（保留初始值） */
function convertQueryValue(raw: string, initial: unknown): unknown {
  if (typeof initial === 'number') {
    const n = Number(raw)
    return raw.trim() !== '' && Number.isFinite(n) ? n : undefined
  }
  if (typeof initial === 'boolean') {
    if (raw === 'true') return true
    if (raw === 'false') return false
    return undefined
  }
  return raw
}

function parsePage(raw: string | null): number {
  if (raw === null || !/^\d+$/.test(raw)) return 1
  const n = Number(raw)
  return n >= 1 ? n : 1
}

export function usePagedList<T, F extends Record<string, unknown>>(
  opts: PagedListOptions<T, F>,
): PagedList<T, F> {
  const route = opts.syncQuery ? useRoute() : null
  const router = opts.syncQuery ? useRouter() : null

  const items = shallowRef<T[]>([]) as Ref<T[]>
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(opts.pageSize ?? 20)
  const filters = reactive({ ...opts.initialFilters }) as F
  const loading = ref(false)
  const error = ref<string | null>(null)

  if (route) {
    const query = route.query
    for (const key of Object.keys(opts.initialFilters)) {
      const raw = firstQueryValue(query[key])
      if (raw === null) continue
      const converted = convertQueryValue(raw, opts.initialFilters[key])
      if (converted !== undefined) (filters as Record<string, unknown>)[key] = converted
    }
    page.value = parsePage(firstQueryValue(query.page))
  }

  let seq = 0

  async function reload(): Promise<void> {
    const mySeq = ++seq
    loading.value = true
    const params = {
      ...cleanFilters(filters),
      page: page.value,
      page_size: pageSize.value,
    } as F & { page: number; page_size: number }
    try {
      const result = await opts.fetch(params)
      if (mySeq !== seq) return
      items.value = result.items
      total.value = result.total
      error.value = null
    } catch (err) {
      if (mySeq !== seq) return
      error.value = errorMessage(err, '載入失敗')
    } finally {
      if (mySeq === seq) loading.value = false
    }
  }

  function writeQuery(): void {
    if (!router || !route) return
    const query: LocationQueryRaw = {}
    for (const [k, v] of Object.entries(cleanFilters(filters))) query[k] = String(v)
    query.page = String(page.value)
    void router.replace({ path: route.path, query })
  }

  function setPage(n: number): void {
    page.value = n
    writeQuery()
    void reload()
  }

  function setFilters(patch: Partial<F>): void {
    Object.assign(filters, patch)
    page.value = 1
    writeQuery()
    void reload()
  }

  if (opts.immediate ?? true) void reload()

  return { items, total, page, pageSize, filters, loading, error, reload, setPage, setFilters }
}
