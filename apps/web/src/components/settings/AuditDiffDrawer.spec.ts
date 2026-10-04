import { flushPromises } from '@vue/test-utils'
import { ElMessage } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import type { AuditLog } from '@/api/auditLogs'
import { mountWithApp } from '@/test/helpers'
import AuditDiffDrawer from './AuditDiffDrawer.vue'

const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/141.0.0.0 Safari/537.36'

function makeLog(overrides: Partial<AuditLog> = {}): AuditLog {
  return {
    id: 'a1',
    created_at: '2026-10-04T01:42:00Z',
    actor_type: 'staff',
    actor_id: '0b6e2d44-1c9f-4a7a-8e55-3f1d2c9b7a10',
    actor_name: '張主任',
    action: 'staff_user.update',
    entity_type: 'staff_user',
    entity_id: '3c2b1a09-8f7e-4d6c-9b5a-4e3d2c1b0a9f',
    before: null,
    after: null,
    ip: '203.0.113.24',
    user_agent: UA,
    ...overrides,
  }
}

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
}

async function mountDrawer(log: AuditLog | null, props: Record<string, unknown> = {}) {
  const { wrapper } = await mountWithApp(AuditDiffDrawer, {
    props: { modelValue: true, log, actionLabel: '更新員工帳號', ...props },
  })
  await settle()
  return wrapper
}

function drawerEl(): HTMLElement {
  const el = document.body.querySelector<HTMLElement>('.el-drawer')
  if (!el) throw new Error('drawer 沒有出現')
  return el
}

function diffRows(): HTMLElement[] {
  return Array.from(drawerEl().querySelectorAll<HTMLElement>('tbody tr.diff-row'))
}

function cells(row: HTMLElement): string[] {
  return Array.from(row.querySelectorAll('td')).map((td) => td.textContent?.replace(/\s+/g, ' ').trim() ?? '')
}

async function toggleShowSame(): Promise<void> {
  const input = drawerEl().querySelector<HTMLInputElement>('.el-switch input')
  if (!input) throw new Error('找不到「顯示未變更欄位」開關')
  input.click()
  await settle()
}

describe('AuditDiffDrawer', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  afterEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it('AuditDiffDrawer shows changed fields only by default', async () => {
    await mountDrawer(
      makeLog({
        before: { display_name: '陳師', phone: '0912-000-111' },
        after: { display_name: '陳老師', phone: '0912-000-111' },
      }),
    )

    expect(diffRows()).toHaveLength(1)
    expect(cells(diffRows()[0]!)).toEqual(['display_name', '陳師', '陳老師'])
    expect(drawerEl().textContent).toContain('變更 1・新增 0・刪除 0・未變更 1')

    await toggleShowSame()

    expect(diffRows()).toHaveLength(2)
    expect(diffRows()[1]!.classList.contains('is-same')).toBe(true)
  })

  it('AuditDiffDrawer marks added and removed fields', async () => {
    await mountDrawer(makeLog({ before: { extra_permissions: ['a'] }, after: { revoked_permissions: ['b'] } }))

    const byField = Object.fromEntries(diffRows().map((r) => [cells(r)[0], r]))
    expect(byField.revoked_permissions!.classList.contains('is-added')).toBe(true)
    expect(byField.extra_permissions!.classList.contains('is-removed')).toBe(true)
    // 沒有該欄位的一側顯示「—」，陣列以 JSON 字串顯示
    expect(cells(byField.revoked_permissions!)).toEqual(['revoked_permissions', '—', '新增 ["b"]'])
    expect(cells(byField.extra_permissions!)).toEqual(['extra_permissions', '刪除 ["a"]', '—'])
  })

  it('AuditDiffDrawer flattens nested objects', async () => {
    await mountDrawer(makeLog({ before: { score: { math: 80 } }, after: { score: { math: 85 } } }))

    expect(diffRows()).toHaveLength(1)
    expect(cells(diffRows()[0]!)).toEqual(['score.math', '80', '85'])
  })

  it('AuditDiffDrawer stringifies values deeper than the second level', async () => {
    await mountDrawer(makeLog({ before: { a: { b: { c: 1 } } }, after: { a: { b: { c: 2 } } } }))

    expect(cells(diffRows()[0]!)).toEqual(['a.b', '{"c":1}', '{"c":2}'])
  })

  it('AuditDiffDrawer empty diff copy', async () => {
    await mountDrawer(makeLog({ before: null, after: null }))

    expect(drawerEl().textContent).toContain('此紀錄沒有欄位變更資料')
    expect(diffRows()).toHaveLength(0)
    expect(drawerEl().querySelector('.el-switch')).toBeNull()
  })

  it('AuditDiffDrawer guides to the switch when every field is unchanged', async () => {
    await mountDrawer(makeLog({ before: { id_number: '***' }, after: { id_number: '***' } }))

    expect(drawerEl().textContent).toContain('沒有欄位內容變更')
    expect(diffRows()).toHaveLength(0)

    await toggleShowSame()

    expect(diffRows()).toHaveLength(1)
    expect(cells(diffRows()[0]!)).toEqual(['id_number', '***', '***'])
  })

  it('AuditDiffDrawer shows masked values as-is and null as empty', async () => {
    await mountDrawer(
      makeLog({
        before: { channel_access_token: '****a91f', channel_secret: null },
        after: { channel_access_token: '****77e1', channel_secret: '********' },
      }),
    )

    const byField = Object.fromEntries(diffRows().map((r) => [cells(r)[0], cells(r)]))
    expect(byField.channel_access_token).toEqual(['channel_access_token', '****a91f', '****77e1'])
    expect(byField.channel_secret).toEqual(['channel_secret', '（空）', '********'])
  })

  it('AuditDiffDrawer shows title and info section', async () => {
    await mountDrawer(makeLog({ before: { a: 1 }, after: { a: 2 } }))

    const text = drawerEl().textContent ?? ''
    expect(drawerEl().querySelector('.el-drawer__header')?.textContent).toContain('更新員工帳號')
    expect(text).toContain('張主任')
    expect(text).toContain('員工')
    expect(text).toContain('staff_user.update')
    expect(text).toContain('員工帳號')
    expect(text).toContain('3c2b1a09-8f7e-4d6c-9b5a-4e3d2c1b0a9f')
    expect(text).toContain('203.0.113.24')
    expect(drawerEl().querySelector('.diff-ua')?.textContent).toBe(UA)
  })

  it('AuditDiffDrawer shows placeholders for missing ip, user agent and actor', async () => {
    await mountDrawer(
      makeLog({
        actor_type: 'system',
        actor_name: null,
        ip: null,
        user_agent: null,
        entity_id: null,
        before: { a: 1 },
        after: { a: 2 },
      }),
    )

    expect(drawerEl().querySelector('.diff-ua')).toBeNull()
    expect(drawerEl().querySelector('[aria-label="複製對象 ID"]')).toBeNull()
    expect(drawerEl().textContent).toContain('系統')
  })

  it('AuditDiffDrawer copies the entity id', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const success = vi.spyOn(ElMessage, 'success').mockReturnValue({ close: vi.fn() })
    await mountDrawer(makeLog({ before: { a: 1 }, after: { a: 2 } }))

    drawerEl().querySelector<HTMLButtonElement>('[aria-label="複製對象 ID"]')!.click()
    await settle()

    expect(writeText).toHaveBeenCalledWith('3c2b1a09-8f7e-4d6c-9b5a-4e3d2c1b0a9f')
    expect(success).toHaveBeenCalledWith('已複製')
  })

  it('AuditDiffDrawer resets the unchanged toggle when the log changes', async () => {
    const wrapper = await mountDrawer(makeLog({ before: { a: 1, b: 1 }, after: { a: 2, b: 1 } }))
    await toggleShowSame()
    expect(diffRows()).toHaveLength(2)

    await wrapper.setProps({ log: makeLog({ id: 'a2', before: { a: 1, b: 1 }, after: { a: 3, b: 1 } }) })
    await settle()

    expect(diffRows()).toHaveLength(1)
  })

  it('AuditDiffDrawer emits update:modelValue false when closed', async () => {
    const wrapper = await mountDrawer(makeLog({ before: { a: 1 }, after: { a: 2 } }))

    drawerEl().querySelector<HTMLButtonElement>('.el-drawer__close-btn')!.click()
    await settle()

    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([false])
  })
})
