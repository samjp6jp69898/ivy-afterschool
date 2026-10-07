import { DOMWrapper, flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { adminHttp } from '@/api/http'
import type { PermissionCatalog, Role } from '@/api/roles'
import { createApiMock, mountWithApp } from '@/test/helpers'
import RoleDetailPanel from './RoleDetailPanel.vue'
import roleDetailPanelSource from './RoleDetailPanel.vue?raw'

// GET /admin/permissions（BACKEND-085）：9 組 28 碼
const CATALOG: PermissionCatalog = {
  groups: [
    { key: 'dashboard', label: '儀表板', permissions: [{ code: 'dashboard:read', label: '首頁儀表板' }] },
    {
      key: 'accounts',
      label: '帳號與權限',
      permissions: [
        { code: 'staff:read', label: '查看員工帳號' },
        { code: 'staff:write', label: '管理員工帳號' },
        { code: 'roles:read', label: '查看角色與權限' },
        { code: 'roles:write', label: '管理角色與權限' },
        { code: 'audit:read', label: '稽核紀錄' },
      ],
    },
    {
      key: 'settings',
      label: '系統設定',
      permissions: [
        { code: 'settings:read', label: '查看系統設定與參考資料' },
        { code: 'settings:write', label: '修改系統設定與參考資料' },
      ],
    },
    {
      key: 'students',
      label: '班級與學生',
      permissions: [
        { code: 'classes:read', label: '查看班級' },
        { code: 'classes:write', label: '管理班級' },
        { code: 'students:read', label: '查看學生' },
        { code: 'students:write', label: '管理學生' },
        { code: 'students:sensitive', label: '查看與寫入身分證、健康備註' },
        { code: 'students:purge', label: '永久刪除（匿名化）退班學生' },
        { code: 'guardians:write', label: '管理監護人與綁定碼' },
      ],
    },
    {
      key: 'attendance',
      label: '出勤',
      permissions: [
        { code: 'attendance:read', label: '查出勤' },
        { code: 'attendance:operate', label: '到班離班登記' },
        { code: 'attendance:amend', label: '改判已登記的出勤（寫 audit）' },
      ],
    },
    {
      key: 'leaves',
      label: '請假',
      permissions: [
        { code: 'leaves:read', label: '查請假' },
        { code: 'leaves:write', label: '代登記與取消請假' },
      ],
    },
    {
      key: 'homework',
      label: '作業進度',
      permissions: [
        { code: 'homework:read', label: '查看作業進度' },
        { code: 'homework:write', label: '更新作業進度' },
      ],
    },
    {
      key: 'pickup',
      label: '接送',
      permissions: [
        { code: 'pickup:read', label: '查看接送佇列' },
        { code: 'pickup:operate', label: '接送回覆、確認、完成' },
        { code: 'pickup:override', label: '代理接送強制完成' },
      ],
    },
    {
      key: 'exams',
      label: '考試成績',
      permissions: [
        { code: 'exams:read', label: '查看成績' },
        { code: 'exams:write', label: '登錄成績' },
        { code: 'exams:publish', label: '發布成績' },
      ],
    },
  ],
}

const ALL_CODES = CATALOG.groups.flatMap((g) => g.permissions.map((p) => p.code)).sort()
const ALL_STAFF = [
  'dashboard:read',
  'classes:read',
  'students:read',
  'attendance:read',
  'attendance:operate',
  'leaves:read',
  'homework:read',
  'homework:write',
  'pickup:read',
  'pickup:operate',
  'exams:read',
]
const CLERK_CODES = [
  ...ALL_STAFF,
  'settings:read',
  'classes:write',
  'students:write',
  'guardians:write',
  'leaves:write',
  'exams:write',
  'exams:publish',
].sort()
const TUTOR_CODES = [...ALL_STAFF, 'exams:write'].sort()

function makeRole(overrides: Partial<Role>): Role {
  return {
    id: `r-${overrides.code ?? 'x'}`,
    code: 'x',
    name: 'x',
    description: null,
    is_system: true,
    permissions: overrides.effective_permissions ?? [],
    effective_permissions: [],
    staff_count: 0,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

const CLERK = makeRole({
  code: 'clerk',
  name: '行政',
  description: '負責註冊、收費與家長聯繫',
  effective_permissions: CLERK_CODES,
  staff_count: 2,
})
const ADMIN = makeRole({ code: 'admin', name: '管理員', permissions: ['*'], effective_permissions: ALL_CODES, staff_count: 1 })
const TUTOR = makeRole({ code: 'tutor', name: '課輔老師', effective_permissions: TUTOR_CODES, staff_count: 4 })
const WEEKEND = makeRole({
  code: 'weekend_helper',
  name: '週六支援老師',
  is_system: false,
  effective_permissions: ['attendance:operate', 'attendance:read', 'pickup:read'],
  staff_count: 0,
})

const MANAGER = ALL_CODES
const READER = ['roles:read']

let mock: MockAdapter

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
}

/** el-form-item 的錯誤訊息有 100ms debounce，斷言錯誤文字前等過 */
async function settleErrors(): Promise<void> {
  await settle()
  await new Promise((resolve) => setTimeout(resolve, 150))
  await settle()
}

async function mountPanel(role: Role, permissions: string[] = MANAGER): Promise<VueWrapper> {
  const { wrapper } = await mountWithApp(RoleDetailPanel, {
    props: { role, catalog: CATALOG },
    piniaInitialState: {
      auth: {
        status: 'authenticated',
        user: {
          id: 'u1',
          username: 'admin',
          display_name: '系統管理員',
          role: { id: 'r-admin', code: 'admin', name: '管理員' },
          permissions: [...permissions].sort(),
          must_change_password: false,
        },
      },
    },
  })
  await settle()
  return wrapper as VueWrapper
}

function formItem(wrapper: VueWrapper, label: string): DOMWrapper<Element> {
  const item = wrapper
    .findAll('.el-form-item')
    .find((i) => i.find('.el-form-item__label').text().trim() === label)
  if (!item) throw new Error(`找不到欄位 ${label}`)
  return item
}

function nameInput(wrapper: VueWrapper): DOMWrapper<HTMLInputElement> {
  return formItem(wrapper, '名稱').find<HTMLInputElement>('input')
}

function button(wrapper: VueWrapper, text: string): DOMWrapper<Element> | undefined {
  return wrapper.findAll('button').find((b) => b.text() === text)
}

async function click(wrapper: VueWrapper, text: string): Promise<void> {
  const btn = button(wrapper, text)
  if (!btn) throw new Error(`找不到按鈕「${text}」`)
  await btn.trigger('click')
  await settle()
}

async function togglePermission(wrapper: VueWrapper, code: string, on: boolean): Promise<void> {
  const item = wrapper.findAll('.perm-item').find((i) => i.find('.perm-item__code').text() === code)
  if (!item) throw new Error(`找不到權限 ${code}`)
  await item.find('input[type=checkbox]').setValue(on)
  await settle()
}

function lastMessageBox(): HTMLElement {
  const box = Array.from(document.body.querySelectorAll<HTMLElement>('.el-message-box')).at(-1)
  if (!box) throw new Error('message box 沒有出現')
  return box
}

async function clickInMessageBox(text: string): Promise<void> {
  const btn = Array.from(lastMessageBox().querySelectorAll<HTMLButtonElement>('button')).find(
    (b) => b.textContent?.trim() === text,
  )
  if (!btn) throw new Error(`message box 找不到「${text}」`)
  btn.click()
  await settle()
}

/** hover 觸發元素後讀出 tooltip 內容（el-tooltip 內容 teleport 到 body） */
async function hoverTooltip(trigger: DOMWrapper<Element>): Promise<string> {
  await trigger.trigger('mouseenter')
  await new Promise((resolve) => setTimeout(resolve, 20))
  await settle()
  const popper = Array.from(document.body.querySelectorAll<HTMLElement>('.el-popper')).at(-1)
  return popper?.textContent?.trim() ?? ''
}

function patchBody(index = 0): Record<string, unknown> {
  const call = mock.history.patch[index]
  if (!call) throw new Error(`沒有第 ${index + 1} 個 PATCH`)
  return JSON.parse(call.data as string) as Record<string, unknown>
}

describe('RoleDetailPanel', () => {
  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    document.body.innerHTML = ''
  })

  it('RoleDetailPanel saves changed permissions only', async () => {
    const expected = [...CLERK_CODES, 'pickup:override'].sort()
    mock.onPatch('/admin/roles/r-clerk').reply(200, { ...CLERK, permissions: expected, effective_permissions: expected })
    const wrapper = await mountPanel(CLERK)

    await togglePermission(wrapper, 'pickup:override', true)
    await click(wrapper, '儲存')

    expect(mock.history.patch.map((c) => c.url)).toEqual(['/admin/roles/r-clerk'])
    expect(patchBody()).toEqual({ permissions: expected })
    expect(document.body.textContent).toContain('角色已更新')
    expect((wrapper.emitted('saved')?.[0]?.[0] as Role).effective_permissions).toEqual(expected)
    expect(button(wrapper, '儲存')?.attributes('disabled')).toBeDefined()
  })

  it('RoleDetailPanel sends trimmed name and cleared description', async () => {
    mock.onPatch('/admin/roles/r-clerk').reply(200, { ...CLERK, name: '行政組長', description: null })
    const wrapper = await mountPanel(CLERK)
    expect(nameInput(wrapper).element.value).toBe('行政')
    expect(formItem(wrapper, '說明').find('textarea').element.value).toBe('負責註冊、收費與家長聯繫')

    await nameInput(wrapper).setValue(' 行政組長 ')
    await formItem(wrapper, '說明').find('textarea').setValue('   ')
    await click(wrapper, '儲存')

    expect(patchBody()).toEqual({ name: '行政組長', description: null })
  })

  it('RoleDetailPanel requires name', async () => {
    const wrapper = await mountPanel(CLERK)

    await nameInput(wrapper).setValue('  ')
    await click(wrapper, '儲存')
    await settleErrors()

    expect(formItem(wrapper, '名稱').find('.el-form-item__error').text()).toBe('請輸入角色名稱')
    expect(mock.history.patch).toHaveLength(0)
  })

  it('RoleDetailPanel shows header, counts and unsaved tag', async () => {
    const wrapper = await mountPanel(CLERK)

    const head = wrapper.find('.role-detail__name')
    expect(head.text()).toContain('行政')
    expect(head.text()).toContain('系統角色')
    expect(wrapper.find('.role-detail__sub').text()).toBe('2 位員工使用・18 項權限')
    expect(formItem(wrapper, '代碼').find('.role-detail__code').text()).toBe('clerk')
    expect(wrapper.find('.role-detail__hint').text()).toBe('權限變更會在員工下一次操作時立即生效')
    expect(head.text()).not.toContain('未儲存')
    expect((wrapper.vm as unknown as { isDirty: boolean }).isDirty).toBe(false)

    await togglePermission(wrapper, 'pickup:override', true)
    expect(head.find('.el-tag--warning').text()).toBe('未儲存')
    expect((wrapper.vm as unknown as { isDirty: boolean }).isDirty).toBe(true)
  })

  it('RoleDetailPanel admin role is readonly', async () => {
    const wrapper = await mountPanel(ADMIN)

    expect(wrapper.text()).toContain('管理員角色擁有全部權限（含未來新增），不可修改')
    expect(button(wrapper, '儲存')).toBeUndefined()
    expect(button(wrapper, '刪除角色')).toBeUndefined()
    expect(nameInput(wrapper).element.disabled).toBe(true)
    expect(wrapper.find('.perm-picker__count').text()).toBe('已選 28 / 28')
    expect(wrapper.findAll<HTMLInputElement>('.perm-item input').every((i) => i.element.disabled)).toBe(true)
  })

  it('RoleDetailPanel readonly without roles:write', async () => {
    const wrapper = await mountPanel(CLERK, READER)

    expect(nameInput(wrapper).element.disabled).toBe(true)
    expect(formItem(wrapper, '說明').find('textarea').element.disabled).toBe(true)
    expect(button(wrapper, '儲存')).toBeUndefined()
    expect(wrapper.find('.role-detail__readonly').text()).toBe('你只有檢視權限')
    document.body.innerHTML = ''

    const custom = await mountPanel(WEEKEND, READER)
    expect(button(custom, '刪除角色')).toBeUndefined()
  })

  it('RoleDetailPanel shows last manager conflict', async () => {
    mock.onPatch('/admin/roles/r-clerk').reply(409, {
      error: { code: 'last_role_manager', message: '此變更會讓系統沒有任何可管理角色權限的啟用帳號', details: null },
    })
    const wrapper = await mountPanel(CLERK)

    await togglePermission(wrapper, 'exams:publish', false)
    await click(wrapper, '儲存')

    const alert = wrapper.find('.role-detail__alert')
    expect(alert.text()).toContain('此變更會讓系統沒有任何可管理角色權限的啟用帳號')
    expect(wrapper.emitted('saved')).toBeUndefined()
    await alert.find('.el-alert__close-btn').trigger('click')
    await settle()
    expect(wrapper.find('.role-detail__alert').exists()).toBe(false)
  })

  it('RoleDetailPanel shows cannot grant alert with labels', async () => {
    mock.onPatch('/admin/roles/r-clerk').reply(403, {
      error: {
        code: 'cannot_grant_permissions',
        message: '不可授出自己沒有的權限',
        details: { permissions: ['pickup:override'] },
      },
    })
    const wrapper = await mountPanel(CLERK)

    await togglePermission(wrapper, 'pickup:override', true)
    await click(wrapper, '儲存')

    const alert = wrapper.find('.role-detail__alert')
    expect(alert.text()).toContain('你沒有以下權限，無法授出：代理接送強制完成')
    // alert 在表單頂端
    const form = wrapper.find('.el-form').element
    expect(alert.element.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
  })

  it('RoleDetailPanel shows other errors as message', async () => {
    mock
      .onPatch('/admin/roles/r-clerk')
      .reply(500, { error: { code: 'internal_error', message: '伺服器暫時沒有回應，請稍後再試', details: null } })
    const wrapper = await mountPanel(CLERK)

    await togglePermission(wrapper, 'pickup:override', true)
    await click(wrapper, '儲存')

    expect(document.body.textContent).toContain('伺服器暫時沒有回應，請稍後再試')
    expect(wrapper.find('.role-detail__alert').exists()).toBe(false)
  })

  it('RoleDetailPanel delete in-use role shows guidance', async () => {
    const inUse = await mountPanel({ ...WEEKEND, staff_count: 3 })
    const disabled = button(inUse, '刪除角色')
    expect(disabled?.attributes('disabled')).toBeDefined()
    expect(await hoverTooltip(inUse.find('.role-detail__delete'))).toBe('仍有 3 位員工使用此角色，請先調整他們的角色')
    inUse.unmount()
    document.body.innerHTML = ''

    mock.onDelete('/admin/roles/r-weekend_helper').reply(409, {
      error: { code: 'role_in_use', message: '仍有員工使用此角色，無法刪除', details: { staff_count: 2 } },
    })
    const wrapper = await mountPanel(WEEKEND)
    await click(wrapper, '刪除角色')
    await clickInMessageBox('刪除')

    expect(mock.history.delete.map((c) => c.url)).toEqual(['/admin/roles/r-weekend_helper'])
    expect(document.body.textContent).toContain('仍有 2 位員工（含已停用的帳號）使用此角色，請先調整他們的角色')
    expect(wrapper.emitted('deleted')).toBeUndefined()
    wrapper.unmount()
    document.body.innerHTML = ''

    const system = await mountPanel(TUTOR)
    expect(button(system, '刪除角色')).toBeUndefined()
  })

  it('RoleDetailPanel deletes unused custom role', async () => {
    mock.onDelete('/admin/roles/r-weekend_helper').reply(204)
    const wrapper = await mountPanel(WEEKEND)

    await click(wrapper, '刪除角色')
    expect(lastMessageBox().textContent).toContain('確定要刪除角色「週六支援老師」嗎？')
    await clickInMessageBox('刪除')

    expect(wrapper.emitted('deleted')).toEqual([['r-weekend_helper']])
    expect(document.body.textContent).toContain('已刪除')
  })

  it('RoleDetailPanel resets form when role changes', async () => {
    const wrapper = await mountPanel(CLERK)
    await nameInput(wrapper).setValue('行政組長')
    expect((wrapper.vm as unknown as { isDirty: boolean }).isDirty).toBe(true)

    await wrapper.setProps({ role: TUTOR })
    await settle()

    expect(nameInput(wrapper).element.value).toBe('課輔老師')
    expect(wrapper.find('.perm-picker__count').text()).toBe('已選 12 / 28')
    expect((wrapper.vm as unknown as { isDirty: boolean }).isDirty).toBe(false)
  })

  it('RoleDetailPanel lays out header and footer per mockup', async () => {
    const wrapper = await mountPanel(CLERK)

    expect(wrapper.find('.role-detail > header.role-detail__head').exists()).toBe(true)
    expect(wrapper.find('.role-detail > footer.role-detail__foot').exists()).toBe(true)
    expect(wrapper.findAll('.role-detail__foot button').map((b) => b.text())).toEqual(['儲存'])
    // happy-dom 不算版面：鎖住稿的名稱字級、代碼等寬字與 footer 提示佔滿左側
    const style = (roleDetailPanelSource.match(/<style scoped>([\s\S]*?)<\/style>/)?.[1] ?? '').replace(/\s+/g, ' ')
    expect(style).toMatch(/\.role-detail__name \{[^}]*font-size: 18px;[^}]*font-weight: 600;/)
    expect(style).toMatch(/\.role-detail__code \{[^}]*font-family: var\(--el-font-family-mono/)
    expect(style).toMatch(/\.role-detail__hint \{[^}]*flex: 1;/)
  })
})
