import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { ApiError, type Page } from '@/shared/types/api'
import { mountWithApp } from '@/test/helpers'
import { usePagedList } from './usePagedList'

interface Row {
  id: string
}

type Params = Record<string, unknown> & { page: number; page_size: number }
type Fetch = (params: Params) => Promise<Page<Row>>

function pageOf(...ids: string[]): Page<Row> {
  return { items: ids.map((id) => ({ id })), total: ids.length }
}

function delayed<T>(ms: number, value: T, fail = false): Promise<T> {
  return new Promise((resolve, reject) => setTimeout(() => (fail ? reject(value) : resolve(value)), ms))
}

async function mountList<F extends Record<string, unknown>>(
  opts: Parameters<typeof usePagedList<Row, F>>[0],
  initialRoute: string,
) {
  let list!: ReturnType<typeof usePagedList<Row, F>>
  const Harness = defineComponent({
    setup() {
      list = usePagedList<Row, F>(opts)
      return () => null
    },
  })
  const stub = { render: () => null }
  const { router } = await mountWithApp(Harness, {
    routes: [
      { path: '/students', component: stub },
      { path: '/s', component: stub },
    ],
    initialRoute,
  })
  return { list, router }
}

describe('usePagedList', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('usePagedList loads first page with cleaned params', async () => {
    const fetch = vi.fn<Fetch>(() => Promise.resolve<Page<Row>>({ items: [{ id: 's1' }], total: 41 }))

    const list = usePagedList<Row, { q: string; status: string; class_id: string | undefined }>({
      fetch,
      initialFilters: { q: '', status: 'active', class_id: undefined },
    })
    await flushPromises()

    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0]?.[0]).toEqual({ status: 'active', page: 1, page_size: 20 })
    expect(list.items.value).toHaveLength(1)
    expect(list.total.value).toBe(41)
    expect(list.loading.value).toBe(false)
    expect(list.error.value).toBeNull()
  })

  it('usePagedList setFilters resets page', async () => {
    const fetch = vi.fn<Fetch>(() => Promise.resolve(pageOf('s1')))
    const list = usePagedList<Row, { q: string }>({ fetch, initialFilters: { q: '' }, pageSize: 50 })
    await flushPromises()

    list.setPage(3)
    await flushPromises()
    list.setFilters({ q: '王' })
    await flushPromises()

    const last = fetch.mock.calls.at(-1)?.[0]
    expect(last).toEqual({ q: '王', page: 1, page_size: 50 })
    expect(list.page.value).toBe(1)
    expect(list.filters.q).toBe('王')
  })

  it('usePagedList setPage refetches the requested page', async () => {
    const fetch = vi.fn<Fetch>(() => Promise.resolve(pageOf('s1')))
    const list = usePagedList<Row, { q: string }>({ fetch, initialFilters: { q: '' } })
    await flushPromises()

    list.setPage(3)
    await flushPromises()

    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls.at(-1)?.[0]).toEqual({ page: 3, page_size: 20 })
    expect(list.page.value).toBe(3)
  })

  it('usePagedList ignores stale responses', async () => {
    vi.useFakeTimers()
    const fetch = vi
      .fn<Fetch>(() => Promise.resolve(pageOf()))
      .mockImplementationOnce(() => delayed(200, pageOf('old')))
      .mockImplementationOnce(() => delayed(10, pageOf('new')))
    const list = usePagedList<Row, { q: string }>({ fetch, initialFilters: { q: '' }, immediate: false })

    list.setFilters({ q: 'a' })
    list.setFilters({ q: 'b' })
    await vi.advanceTimersByTimeAsync(300)

    expect(fetch).toHaveBeenCalledTimes(2)
    expect(list.items.value[0]?.id).toBe('new')
    expect(list.items.value).toHaveLength(1)
    expect(list.loading.value).toBe(false)
  })

  it('usePagedList stale results never touch loading or error', async () => {
    vi.useFakeTimers()
    const fetch = vi
      .fn<Fetch>(() => Promise.resolve(pageOf()))
      .mockImplementationOnce(() => delayed(50, new ApiError(500, 'x', '伺服器錯誤'), true) as Promise<never>)
      .mockImplementationOnce(() => delayed(100, pageOf('new')))
    const list = usePagedList<Row, { q: string }>({ fetch, initialFilters: { q: '' }, immediate: false })

    list.setFilters({ q: 'a' })
    list.setFilters({ q: 'b' })
    await vi.advanceTimersByTimeAsync(60)
    expect(list.error.value).toBeNull()
    expect(list.loading.value).toBe(true)

    await vi.advanceTimersByTimeAsync(60)
    expect(list.items.value[0]?.id).toBe('new')
    expect(list.error.value).toBeNull()
    expect(list.loading.value).toBe(false)
  })

  it('usePagedList keeps items and sets error on failure', async () => {
    const fetch = vi
      .fn<Fetch>(() => Promise.resolve(pageOf('s1')))
      .mockImplementationOnce(() => Promise.resolve(pageOf('s1')))
      .mockImplementationOnce(() => Promise.reject(new ApiError(500, 'x', '伺服器錯誤')))
    const list = usePagedList<Row, { q: string }>({ fetch, initialFilters: { q: '' } })
    await flushPromises()

    await list.reload()

    expect(list.error.value).toBe('伺服器錯誤')
    expect(list.items.value.map((r) => r.id)).toEqual(['s1'])
    expect(list.total.value).toBe(1)
    expect(list.loading.value).toBe(false)
  })

  it('usePagedList clears error after a later success', async () => {
    const fetch = vi
      .fn<Fetch>(() => Promise.resolve(pageOf('s2')))
      .mockImplementationOnce(() => Promise.reject(new ApiError(500, 'x', '伺服器錯誤')))
    const list = usePagedList<Row, { q: string }>({ fetch, initialFilters: { q: '' } })
    await flushPromises()
    expect(list.error.value).toBe('伺服器錯誤')

    await list.reload()

    expect(list.error.value).toBeNull()
    expect(list.items.value.map((r) => r.id)).toEqual(['s2'])
  })

  it('usePagedList uses fallback message for unknown errors', async () => {
    const fetch = vi.fn<Fetch>(() => Promise.reject(new Error('boom')))
    const list = usePagedList<Row, { q: string }>({ fetch, initialFilters: { q: '' } })
    await flushPromises()

    expect(list.error.value).toBe('載入失敗')
  })

  it('usePagedList waits for reload when immediate is false', async () => {
    const fetch = vi.fn<Fetch>(() => Promise.resolve(pageOf('s1')))
    const list = usePagedList<Row, { q: string }>({ fetch, initialFilters: { q: '' }, immediate: false })
    await flushPromises()
    expect(fetch).toHaveBeenCalledTimes(0)

    await list.reload()

    expect(fetch).toHaveBeenCalledTimes(1)
    expect(list.items.value.map((r) => r.id)).toEqual(['s1'])
  })

  it('usePagedList syncs with route query', async () => {
    const fetch = vi.fn<Fetch>(() => Promise.resolve(pageOf('s1')))
    const { list, router } = await mountList<{ status: string; q: string }>(
      { fetch, initialFilters: { status: 'active', q: '' }, syncQuery: true },
      '/students?page=2&status=suspended',
    )

    expect(fetch.mock.calls[0]?.[0]).toEqual({ status: 'suspended', page: 2, page_size: 20 })
    expect(list.page.value).toBe(2)

    list.setFilters({ status: 'active' })
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/students')
    expect(router.currentRoute.value.query).toEqual({ status: 'active', page: '1' })
  })

  it('usePagedList converts query values by initialFilters type', async () => {
    type F = { status: string; academic_year: number; archived: boolean }
    const initialFilters: F = { status: 'active', academic_year: 115, archived: false }

    const fetch = vi.fn<Fetch>(() => Promise.resolve(pageOf('s1')))
    const { list, router } = await mountList<F>(
      { fetch, initialFilters, syncQuery: true },
      '/s?academic_year=114&archived=true&page=x&unknown=1',
    )
    expect(fetch.mock.calls[0]?.[0]).toEqual({
      status: 'active',
      academic_year: 114,
      archived: true,
      page: 1,
      page_size: 20,
    })

    list.setPage(2)
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({
      status: 'active',
      academic_year: '114',
      archived: 'true',
      page: '2',
    })

    const fetch2 = vi.fn<Fetch>(() => Promise.resolve(pageOf('s1')))
    await mountList<F>({ fetch: fetch2, initialFilters, syncQuery: true }, '/s?academic_year=abc')
    expect(fetch2.mock.calls[0]?.[0]).toMatchObject({ academic_year: 115, page: 1 })
  })
})
