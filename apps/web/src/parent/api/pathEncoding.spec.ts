import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createApiMock } from '@/test/helpers'
import { getChildAttendance } from './attendance'
import { getChild, getChildToday } from './children'
import { getChildExam, listChildExams } from './exams'
import { getChildHomework } from './homework'
import { parentHttp } from './http'
import { cancelLeave, listChildLeaves, uploadLeaveAttachment } from './leaves'
import { markNotificationRead } from './notifications'
import { createPickupPerson, deletePickupPerson, listPickupPersons } from './pickupPersons'
import { cancelPickupRequest, markPickupArrived } from './pickupRequests'

// 路由參數 / 外部輸入會流進路徑的 id：不可改變路徑結構（例如跳到別的 endpoint）
const EVIL = '../../me?x=1#y'
const UUID_A = '3f2b8c1e-5a4d-4c7e-9b0a-1d2e3f4a5b6c'
const UUID_B = '9a7d6e5f-4b3c-4d2e-8f1a-0b9c8d7e6f5a'

interface PathCase {
  name: string
  method: 'get' | 'post' | 'delete'
  /** 把待測的 id 放在這個函式的路徑 id 位置呼叫（兩個 id 的函式，另一個 id 用正常 UUID） */
  call: (id: string) => Promise<unknown>
  /** 預期的請求 URL；segment 是路徑裡該 id 位置應出現的字串 */
  url: (segment: string) => string
}

const PERSON = { name: '李阿姨', relation: '保母', phone: '0912000123' }

const CASES: PathCase[] = [
  { name: 'getChild', method: 'get', call: (id) => getChild(id), url: (s) => `/parent/children/${s}` },
  { name: 'getChildToday', method: 'get', call: (id) => getChildToday(id), url: (s) => `/parent/children/${s}/today` },
  {
    name: 'markPickupArrived',
    method: 'post',
    call: (id) => markPickupArrived(id),
    url: (s) => `/parent/pickup/requests/${s}/arrived`,
  },
  {
    name: 'cancelPickupRequest',
    method: 'post',
    call: (id) => cancelPickupRequest(id, '臨時有事'),
    url: (s) => `/parent/pickup/requests/${s}/cancel`,
  },
  {
    name: 'listPickupPersons',
    method: 'get',
    call: (id) => listPickupPersons(id),
    url: (s) => `/parent/children/${s}/pickup-persons`,
  },
  {
    name: 'createPickupPerson',
    method: 'post',
    call: (id) => createPickupPerson(id, PERSON),
    url: (s) => `/parent/children/${s}/pickup-persons`,
  },
  {
    name: 'deletePickupPerson',
    method: 'delete',
    call: (id) => deletePickupPerson(id),
    url: (s) => `/parent/pickup-persons/${s}`,
  },
  {
    name: 'getChildAttendance',
    method: 'get',
    call: (id) => getChildAttendance(id, '2026-10'),
    url: (s) => `/parent/children/${s}/attendance`,
  },
  { name: 'listChildExams', method: 'get', call: (id) => listChildExams(id), url: (s) => `/parent/children/${s}/exams` },
  {
    name: 'getChildExam (child id)',
    method: 'get',
    call: (id) => getChildExam(id, UUID_B),
    url: (s) => `/parent/children/${s}/exams/${UUID_B}`,
  },
  {
    name: 'getChildExam (exam id)',
    method: 'get',
    call: (id) => getChildExam(UUID_A, id),
    url: (s) => `/parent/children/${UUID_A}/exams/${s}`,
  },
  {
    name: 'getChildHomework',
    method: 'get',
    call: (id) => getChildHomework(id),
    url: (s) => `/parent/children/${s}/homework`,
  },
  {
    name: 'markNotificationRead',
    method: 'post',
    call: (id) => markNotificationRead(id),
    url: (s) => `/parent/notifications/${s}/read`,
  },
  {
    name: 'listChildLeaves',
    method: 'get',
    call: (id) => listChildLeaves(id),
    url: (s) => `/parent/children/${s}/leaves`,
  },
  { name: 'cancelLeave', method: 'post', call: (id) => cancelLeave(id), url: (s) => `/parent/leaves/${s}/cancel` },
  {
    name: 'uploadLeaveAttachment',
    method: 'post',
    call: (id) => uploadLeaveAttachment(id, new File(['x'], 'note.pdf', { type: 'application/pdf' })),
    url: (s) => `/parent/leaves/${s}/attachments`,
  },
]

/**
 * 找出 api client 原始碼中不安全的路徑插值：template literal 的每個 `${...}` 都必須是
 * encodeURIComponent(...) 或 *Path(...)（把 id 編碼後組路徑的小函式）。先去掉註解，避免註解裡的反引號干擾。
 */
function unsafeInterpolations(source: string): string[] {
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/.*$/gm, '$1')
  const offenders: string[] = []
  for (const literal of code.matchAll(/`([^`]*)`/g)) {
    for (const expression of (literal[1] ?? '').matchAll(/\$\{([^}]*)\}/g)) {
      const text = (expression[1] ?? '').trim()
      if (!/^(encodeURIComponent|\w+Path)\(.+\)$/.test(text)) offenders.push(text)
    }
  }
  return offenders
}

const sources = import.meta.glob<string>('./*.ts', { query: '?raw', import: 'default', eager: true })

describe('parent api path encoding', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = createApiMock(parentHttp)
    mock.onAny().reply(200, {})
  })

  afterEach(() => {
    mock.restore()
  })

  it('parent api path encoding encodes unsafe ids', async () => {
    const encoded = encodeURIComponent(EVIL)

    for (const c of CASES) {
      mock.resetHistory()
      await c.call(EVIL)

      const urls = mock.history[c.method]!.map((r) => r.url)
      expect(urls, c.name).toEqual([c.url(encoded)])
      const url = urls[0]!
      // 不含未編碼的 '?'、'#'，路徑層數與一般 UUID 相同（沒有被 '/' 切成別的 endpoint）
      expect(url, c.name).not.toMatch(/[?#]/)
      expect(url.split('/'), c.name).toHaveLength(c.url(UUID_A).split('/').length)
      expect(url, c.name).toContain('..%2F..%2Fme%3Fx%3D1%23y')
    }
  })

  it('parent api path encoding keeps normal uuid urls unchanged', async () => {
    for (const c of CASES) {
      mock.resetHistory()
      await c.call(UUID_A)

      expect(
        mock.history[c.method]!.map((r) => r.url),
        c.name,
      ).toEqual([c.url(UUID_A)])
    }
  })

  it('parent api path encoding has no raw interpolation in api paths', () => {
    const clientFiles = Object.entries(sources).filter(([file]) => !file.endsWith('.spec.ts'))

    // 確認掃描真的讀到各個 client（避免 glob 失效造成空轉）
    const names = clientFiles.map(([file]) => file.replace('./', ''))
    const expected = [
      'attendance.ts',
      'children.ts',
      'exams.ts',
      'homework.ts',
      'leaves.ts',
      'notifications.ts',
      'pickupPersons.ts',
      'pickupRequests.ts',
    ]
    for (const name of expected) expect(names).toContain(name)
    for (const [file, source] of clientFiles) {
      expect(unsafeInterpolations(source), file).toEqual([])
    }
  })

  it('parent api path encoding checker flags raw interpolation', () => {
    expect(unsafeInterpolations('const p = `/parent/x/${id}`')).toEqual(['id'])
    expect(unsafeInterpolations('const p = `/parent/x/${encodeURIComponent(a)}/${b}`')).toEqual(['b'])
    expect(unsafeInterpolations('const p = `/parent/x/${encodeURIComponent(id)}/y`')).toEqual([])
    expect(unsafeInterpolations('const p = `${leavePath(id)}/cancel`')).toEqual([])
    expect(unsafeInterpolations('const p = `/parent/x/${id.toString()}`')).toEqual(['id.toString()'])
    // 註解裡的反引號與插值不算
    expect(unsafeInterpolations('/** 例如 `${raw}` */\n// `${raw2}`\nconst a = 1')).toEqual([])
  })
})
