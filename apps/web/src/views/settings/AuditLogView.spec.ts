import { flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import type { AuditLog } from '@/api/auditLogs'
import { adminHttp } from '@/api/http'
import { VIEW_LOADERS } from '@/router/viewLoaders'
import { createApiMock, mountWithApp } from '@/test/helpers'
import AuditLogView from './AuditLogView.vue'

const NOW = new Date('2026-10-02T03:00:00Z') // 台北 2026-10-02 11:00

function makeLog(overrides: Partial<AuditLog> = {}): AuditLog {
  return {
    id: 'a1',
    created_at: '2026-10-02T02:00:00Z',
    actor_type: 'staff',
    actor_id: '3c2b1a09-8f7e-4d6c-9b5a-4e3d2c1b0a9f',
    actor_name: '王主任',
    action: 'staff_user.update',
    entity_type: 'staff_user',
    entity_id: '7d1e4f20-5a6b-4c3d-8e9f-0a1b2c3d4e5f',
    before: { display_name: '陳師' },
    after: { display_name: '陳老師' },
    ip: '203.0.113.24',
    user_agent: 'Mozilla/5.0',
    ...overrides,
  }
}

let mock: MockAdapter

function replyLogs(items: AuditLog[], total = items.length): void {
  mock.onGet('/admin/audit-logs').reply(200, { items, total })
}

function getParams(): Record<string, unknown>[] {
  return mock.history.get.map((c) => c.params as Record<string, unknown>)
}

function lastParams(): Record<string, unknown> {
  const p = getParams().at(-1)
  if (!p) throw new Error('沒有 GET /admin/audit-logs 請求')
  return p
}

async function mountView(initialRoute = '/settings/audit') {
  const mounted = await mountWithApp(AuditLogView, { initialRoute })
  await flushPromises()
  return mounted
}

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
}

function filterSelect(wrapper: VueWrapper, name: string) {
  const select = wrapper.find(`[data-test=filter-${name}] .el-select`)
  if (!select.exists()) throw new Error(`找不到篩選 ${name}`)
  return select
}

/** 打開下拉後點選文字完全相同的選項（各篩選的選項文字互不重複，「全部」除外） */
async function chooseOption(wrapper: VueWrapper, name: string, label: string): Promise<void> {
  await filterSelect(wrapper, name).find('.el-select__wrapper').trigger('click')
  await settle()
  const options = Array.from(document.body.querySelectorAll<HTMLElement>('.el-select-dropdown__item'))
  const option = options.find((o) => o.textContent?.trim() === label)
  if (!option) throw new Error(`找不到選項「${label}」，現有：${options.map((o) => o.textContent?.trim()).join('、')}`)
  option.click()
  await settle()
}

function tableRows(wrapper: VueWrapper) {
  return wrapper.findAll('.el-table__body tr.el-table__row')
}

function rowText(wrapper: VueWrapper, index: number): string {
  return tableRows(wrapper).at(index)?.text().replace(/\s+/g, ' ') ?? ''
}

function drawerTitle(): string {
  return document.body.querySelector('.el-drawer__title')?.textContent?.trim() ?? ''
}

describe('AuditLogView', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('AuditLogView defaults to last 7 days', async () => {
    replyLogs([])

    const { wrapper } = await mountView()

    expect(getParams()).toHaveLength(1)
    expect(getParams()[0]).toEqual({ date_from: '2026-09-26', date_to: '2026-10-02', page: 1, page_size: 50 })
    const dateInputs = wrapper.findAll('[data-test=filter-date] input')
    expect(dateInputs.map((i) => (i.element as HTMLInputElement).value)).toEqual(['2026-09-26', '2026-10-02'])
  })

  it('AuditLogView defaults to last 7 days across taipei midnight', async () => {
    // UTC 16:00 = 台北隔天 00:00
    vi.setSystemTime(new Date('2026-10-01T16:00:00Z'))
    replyLogs([])

    await mountView()

    expect(lastParams()).toMatchObject({ date_from: '2026-09-26', date_to: '2026-10-02' })
  })

  it('AuditLogView filters by action category', async () => {
    replyLogs([makeLog()], 120)
    const { router, wrapper } = await mountView('/settings/audit?page=3')
    expect(lastParams().page).toBe(3)

    await chooseOption(wrapper, 'action', '出勤改判')

    expect(lastParams()).toEqual({
      action_prefix: 'attendance.',
      date_from: '2026-09-26',
      date_to: '2026-10-02',
      page: 1,
      page_size: 50,
    })
    expect(router.currentRoute.value.query).toMatchObject({ action_prefix: 'attendance.', page: '1' })
  })

  it('AuditLogView filters exam publish category', async () => {
    replyLogs([])
    const { wrapper } = await mountView()

    await chooseOption(wrapper, 'action', '考試發布')

    expect(lastParams().action_prefix).toBe('exam.')
  })

  it('AuditLogView lists all nine action categories after all', async () => {
    replyLogs([])
    const { wrapper } = await mountView()

    await filterSelect(wrapper, 'action').find('.el-select__wrapper').trigger('click')
    await settle()
    const labels = Array.from(document.body.querySelectorAll('.el-select-dropdown__item')).map((o) =>
      o.textContent?.trim(),
    )

    expect(labels).toEqual([
      '全部',
      '帳號',
      '角色',
      '系統設定',
      '學生',
      '監護人',
      '出勤改判',
      '成績修改',
      '考試發布',
      '接送強制完成 / 目視核對',
    ])
  })

  it('AuditLogView filters by actor type and back to all', async () => {
    replyLogs([])
    const { wrapper } = await mountView()

    await chooseOption(wrapper, 'actor', '家長')
    expect(lastParams().actor_type).toBe('parent')

    await chooseOption(wrapper, 'actor', '全部')
    expect(lastParams()).not.toHaveProperty('actor_type')
  })

  it('AuditLogView entity type select with free input', async () => {
    replyLogs([])
    const { wrapper } = await mountView()

    await chooseOption(wrapper, 'entity', '出勤')
    expect(lastParams().entity_type).toBe('student_attendance')

    const select = filterSelect(wrapper, 'entity')
    await select.find('.el-select__wrapper').trigger('click')
    await settle()
    const input = select.find('input')
    await input.setValue('student_leave')
    await settle()
    await input.trigger('keydown', { key: 'Enter', code: 'Enter' })
    await settle()

    expect(lastParams().entity_type).toBe('student_leave')
    expect(lastParams().page).toBe(1)
  })

  it('AuditLogView renders action labels and opens drawer', async () => {
    replyLogs([
      makeLog({ action: 'staff_user.update', actor_name: '王主任', actor_type: 'staff', created_at: '2026-10-02T02:00:00Z' }),
    ])
    const { wrapper } = await mountView()

    const text = rowText(wrapper, 0)
    expect(text).toContain('修改員工帳號')
    expect(text).toContain('staff_user.update')
    expect(text).toContain('王主任')
    expect(text).toContain('員工')
    expect(text).toContain('2026/10/02 10:00')

    await tableRows(wrapper).at(0)!.trigger('click')
    await settle()

    expect(drawerTitle()).toContain('修改員工帳號')
  })

  it('AuditLogView shows raw action and entity type when unknown', async () => {
    replyLogs([makeLog({ action: 'lunch.order', entity_type: 'lunch_menu', entity_id: '42' })])
    const { wrapper } = await mountView()

    expect(wrapper.find('[data-test=action-label]').exists()).toBe(false)
    expect(rowText(wrapper, 0)).toContain('lunch.order')
    expect(rowText(wrapper, 0)).toContain('lunch_menu')

    await wrapper.find('[data-test=open-detail]').trigger('click')
    await settle()
    expect(drawerTitle()).toBe('lunch.order')
  })

  it('AuditLogView detail link opens drawer', async () => {
    replyLogs([makeLog({ action: 'settings.update', entity_type: 'system_setting', entity_id: 'pickup.window' })])
    const { wrapper } = await mountView()

    await wrapper.find('[data-test=open-detail]').trigger('click')
    await settle()

    expect(drawerTitle()).toContain('修改系統設定')
  })

  it('AuditLogView empty state', async () => {
    mock.onGet('/admin/audit-logs').reply(200, { items: [], total: 0 })

    const { wrapper } = await mountView()

    expect(wrapper.text()).toContain('這段期間沒有稽核紀錄')
    expect(wrapper.text()).toContain('共 0 筆')
    expect(wrapper.find('.el-pagination').exists()).toBe(false)
  })

  it('AuditLogView shortens only uuid entity ids', async () => {
    replyLogs([
      makeLog({ id: 'a1', entity_type: 'system_setting', entity_id: 'pickup.window' }),
      makeLog({ id: 'a2', entity_type: 'role', entity_id: '0b6e2d44-1c9f-4a7a-8e55-3f1d2c9b7a10' }),
    ])
    const { wrapper } = await mountView()

    expect(rowText(wrapper, 0)).toContain('系統設定')
    expect(rowText(wrapper, 0)).toContain('pickup.window')
    expect(rowText(wrapper, 1)).toContain('角色')
    expect(rowText(wrapper, 1)).toContain('0b6e2d44')
    expect(rowText(wrapper, 1)).not.toContain('0b6e2d44-1c9f')
  })

  it('AuditLogView paginates with total', async () => {
    replyLogs([makeLog()], 137)
    const { wrapper } = await mountView()

    expect(wrapper.text()).toContain('共 137 筆')
    await wrapper.find('.el-pagination .btn-next').trigger('click')
    await settle()

    expect(lastParams().page).toBe(2)
    expect(lastParams().page_size).toBe(50)
  })

  it('AuditLogView reset restores default filters', async () => {
    replyLogs([])
    const { wrapper } = await mountView()
    const reset = wrapper.find('[data-test=reset-filters]')
    expect(reset.attributes('disabled')).toBeDefined()

    await chooseOption(wrapper, 'actor', '系統')
    expect(reset.attributes('disabled')).toBeUndefined()

    await reset.trigger('click')
    await settle()

    expect(lastParams()).toEqual({ date_from: '2026-09-26', date_to: '2026-10-02', page: 1, page_size: 50 })
    expect(reset.attributes('disabled')).toBeDefined()
  })

  it('AuditLogView shows error state with retry', async () => {
    mock.onGet('/admin/audit-logs').replyOnce(500, {
      error: { code: 'internal_error', message: '伺服器錯誤', details: null },
    })
    const { wrapper } = await mountView()

    expect(wrapper.text()).toContain('無法載入稽核紀錄')
    expect(wrapper.find('.el-table').exists()).toBe(false)

    replyLogs([makeLog()])
    await wrapper.find('[data-test=retry]').trigger('click')
    await settle()

    expect(getParams()).toHaveLength(2)
    expect(rowText(wrapper, 0)).toContain('修改員工帳號')
  })

  it('AuditLogView route loader points at the view', async () => {
    const loaded = (await VIEW_LOADERS.AuditLogView()) as { default?: unknown }

    expect(loaded.default).toBe(AuditLogView)
  })
})
