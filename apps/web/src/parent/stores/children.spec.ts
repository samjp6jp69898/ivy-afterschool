import type MockAdapter from 'axios-mock-adapter'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApiMock } from '@/test/helpers'
import type { ChildSummary } from '../api/children'
import { parentHttp } from '../api/http'
import { useChildrenStore } from './children'

const STORAGE_KEY = 'parent_selected_child_v1'
const LOAD_ERROR = '小孩清單載入失敗'

function child(id: string, name: string, overrides: Partial<ChildSummary> = {}): ChildSummary {
  return {
    id,
    name,
    grade_level: 3,
    class_name: '三年級班',
    school_name: '快樂國小',
    photo_url: null,
    status: 'active',
    ...overrides,
  }
}

const MING = child('s1', '王小明')
const HUA = child('s2', '王小華')
const SERVER_ERROR = { error: { code: 'internal_error', message: '伺服器發生錯誤', details: null } }

describe('childrenStore', () => {
  let mock: MockAdapter

  beforeEach(() => {
    setActivePinia(createPinia())
    mock = createApiMock(parentHttp)
  })

  afterEach(() => {
    mock.restore()
  })

  /** 讓下一個 GET /parent/children 停住，測試手動放行（可指定 data 與狀態碼） */
  function holdReply(): { release: (data: unknown, status?: number) => Promise<void> } {
    let resolveReply!: (value: [number, unknown]) => void
    let reached!: () => void
    const reachedPromise = new Promise<void>((resolve) => {
      reached = resolve
    })
    mock.onGet('/parent/children').replyOnce(() => {
      reached()
      return new Promise<[number, unknown]>((resolve) => {
        resolveReply = resolve
      })
    })
    return {
      release: async (data, status = 200) => {
        await reachedPromise
        resolveReply([status, data])
      },
    }
  }

  it('childrenStore load selects first child', async () => {
    mock.onGet('/parent/children').reply(200, [MING, HUA])
    const store = useChildrenStore()

    await store.load()

    expect(store.items).toEqual([MING, HUA])
    expect(store.loaded).toBe(true)
    expect(store.loading).toBe(false)
    expect(store.error).toBe('')
    expect(store.selectedId).toBe('s1')
    expect(store.selectedChild?.name).toBe('王小明')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('s1')
  })

  it('childrenStore keeps stored selection', async () => {
    localStorage.setItem(STORAGE_KEY, 's2')
    mock.onGet('/parent/children').reply(200, [MING, HUA])
    const store = useChildrenStore()
    // 清單載入前先還原 selectedId，但還找不到對應小孩
    expect(store.selectedId).toBe('s2')
    expect(store.selectedChild).toBeNull()

    await store.load()

    expect(store.selectedChild?.name).toBe('王小華')
    expect(store.selectedId).toBe('s2')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('s2')
  })

  it('childrenStore drops stale stored selection', async () => {
    localStorage.setItem(STORAGE_KEY, 's9')
    mock.onGet('/parent/children').reply(200, [MING, HUA])
    const store = useChildrenStore()

    await store.load()

    expect(store.selectedId).toBe('s1')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('s1')
  })

  it('childrenStore select ignores unknown id', async () => {
    mock.onGet('/parent/children').reply(200, [MING, HUA])
    const store = useChildrenStore()
    await store.load()

    store.select('s9')
    expect(store.selectedId).toBe('s1')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('s1')

    store.select('s2')
    expect(store.selectedId).toBe('s2')
    expect(store.selectedChild?.name).toBe('王小華')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('s2')
  })

  it('childrenStore ignores stale response', async () => {
    const held = holdReply()
    const store = useChildrenStore()

    const pending = store.load()
    expect(store.loading).toBe(true)
    store.clear()
    await held.release([MING])
    await pending

    expect(store.items).toHaveLength(0)
    expect(store.loaded).toBe(false)
    expect(store.loading).toBe(false)
    expect(store.selectedId).toBeNull()
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('childrenStore load error keeps items', async () => {
    const store = useChildrenStore()
    store.seed([MING])
    mock.onGet('/parent/children').reply(500, SERVER_ERROR)

    await store.load(true)

    expect(store.error).toBe(LOAD_ERROR)
    expect(store.items).toHaveLength(1)
    expect(store.items[0]!.name).toBe('王小明')
    expect(store.loaded).toBe(true)
    expect(store.loading).toBe(false)
    expect(store.selectedId).toBe('s1')
  })

  it('childrenStore load clears the error after a successful retry', async () => {
    mock.onGet('/parent/children').replyOnce(500, SERVER_ERROR).onGet('/parent/children').replyOnce(200, [MING])
    const store = useChildrenStore()

    await store.load()
    expect(store.error).toBe(LOAD_ERROR)
    expect(store.loaded).toBe(false)
    expect(store.items).toHaveLength(0)

    await store.load()

    expect(store.error).toBe('')
    expect(store.loaded).toBe(true)
    expect(store.items).toEqual([MING])
  })

  it('childrenStore load skips refetch when loaded unless forced', async () => {
    mock.onGet('/parent/children').reply(200, [MING, HUA])
    const store = useChildrenStore()

    await store.load()
    await store.load()
    expect(mock.history.get).toHaveLength(1)

    await store.load(true)
    expect(mock.history.get).toHaveLength(2)
  })

  it('childrenStore load shares the in-flight request', async () => {
    const held = holdReply()
    const store = useChildrenStore()

    const first = store.load()
    const second = store.load()
    await held.release([MING, HUA])
    await Promise.all([first, second])

    expect(mock.history.get).toHaveLength(1)
    expect(store.items).toEqual([MING, HUA])
    expect(store.loaded).toBe(true)
  })

  it('childrenStore force reload supersedes an older in-flight request', async () => {
    const older = holdReply()
    const newer = holdReply()
    const store = useChildrenStore()

    const first = store.load()
    const second = store.load(true)
    await newer.release([HUA])
    await second
    expect(store.items).toEqual([HUA])

    // 較舊的請求晚到：結果被丟棄，不蓋掉較新的清單
    await older.release([MING])
    await first

    expect(store.items).toEqual([HUA])
    expect(store.selectedId).toBe('s2')
    expect(store.loading).toBe(false)
  })

  it('childrenStore seed fills items without a request', async () => {
    localStorage.setItem(STORAGE_KEY, 's2')
    const store = useChildrenStore()

    store.seed([MING, HUA])

    expect(store.items).toEqual([MING, HUA])
    expect(store.loaded).toBe(true)
    expect(store.selectedId).toBe('s2')

    await store.load()
    expect(mock.history.get).toHaveLength(0)
    expect(store.error).toBe('')
  })

  it('childrenStore seed discards an in-flight load', async () => {
    const held = holdReply()
    const store = useChildrenStore()

    const pending = store.load()
    store.seed([HUA])
    await held.release([MING])
    await pending

    expect(store.items).toEqual([HUA])
    expect(store.selectedId).toBe('s2')
    expect(store.loading).toBe(false)
  })

  it('childrenStore falls back to the first child when the selected one disappears', async () => {
    mock.onGet('/parent/children').replyOnce(200, [MING, HUA]).onGet('/parent/children').replyOnce(200, [MING])
    const store = useChildrenStore()
    await store.load()
    store.select('s2')

    await store.load(true)

    expect(store.items).toEqual([MING])
    expect(store.selectedId).toBe('s1')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('s1')
  })

  it('childrenStore clears the selection for an empty list', async () => {
    localStorage.setItem(STORAGE_KEY, 's1')
    mock.onGet('/parent/children').reply(200, [])
    const store = useChildrenStore()

    await store.load()

    expect(store.loaded).toBe(true)
    expect(store.selectedId).toBeNull()
    expect(store.selectedChild).toBeNull()
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('childrenStore hasMultiple follows the item count', () => {
    const store = useChildrenStore()
    expect(store.hasMultiple).toBe(false)

    store.seed([MING])
    expect(store.hasMultiple).toBe(false)

    store.seed([MING, HUA])
    expect(store.hasMultiple).toBe(true)
  })

  it('childrenStore clear resets state and removes the stored selection', async () => {
    mock.onGet('/parent/children').replyOnce(200, [MING, HUA]).onGet('/parent/children').replyOnce(500, SERVER_ERROR)
    const store = useChildrenStore()
    await store.load()
    await store.load(true)
    expect(store.error).toBe(LOAD_ERROR)
    expect(localStorage.getItem(STORAGE_KEY)).toBe('s1')

    store.clear()

    expect(store.items).toEqual([])
    expect(store.loaded).toBe(false)
    expect(store.loading).toBe(false)
    expect(store.error).toBe('')
    expect(store.selectedId).toBeNull()
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('childrenStore still works when localStorage throws', async () => {
    const broken = {
      getItem: () => {
        throw new Error('storage disabled')
      },
      setItem: () => {
        throw new Error('storage disabled')
      },
      removeItem: () => {
        throw new Error('storage disabled')
      },
    }
    vi.stubGlobal('localStorage', broken)
    mock.onGet('/parent/children').reply(200, [MING, HUA])

    const store = useChildrenStore()
    expect(store.selectedId).toBeNull()

    await store.load()
    expect(store.selectedId).toBe('s1')

    store.select('s2')
    expect(store.selectedId).toBe('s2')

    store.clear()
    expect(store.selectedId).toBeNull()
  })
})
