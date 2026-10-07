import { DOMWrapper, flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { adminHttp } from '@/api/http'
import type { PermissionCatalog, Role } from '@/api/roles'
import { createApiMock, mountWithApp } from '@/test/helpers'
import RoleCreateDialog from './RoleCreateDialog.vue'
import roleCreateDialogSource from './RoleCreateDialog.vue?raw'

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

// domain_spec §3 的預設授予
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
const TUTOR = [...ALL_STAFF, 'exams:write'].sort()
const CLERK = [
  ...ALL_STAFF,
  'settings:read',
  'classes:write',
  'students:write',
  'guardians:write',
  'leaves:write',
  'exams:write',
  'exams:publish',
].sort()
const DIRECTOR = [
  ...ALL_STAFF,
  'staff:read',
  'roles:read',
  'settings:read',
  'settings:write',
  'audit:read',
  'classes:write',
  'students:write',
  'students:sensitive',
  'guardians:write',
  'attendance:amend',
  'leaves:write',
  'pickup:override',
  'exams:write',
  'exams:publish',
].sort()
const ALL_CODES = CATALOG.groups.flatMap((g) => g.permissions.map((p) => p.code)).sort()
const ROLE_MANAGER = ['roles:read', 'roles:write']

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

const ROLES: Role[] = [
  makeRole({ code: 'tutor', name: '課輔老師', effective_permissions: TUTOR }),
  makeRole({ code: 'weekend_helper', name: '週六支援老師', is_system: false, effective_permissions: ['attendance:read', 'pickup:read'] }),
  makeRole({ code: 'clerk', name: '行政', effective_permissions: CLERK }),
  makeRole({ code: 'admin', name: '管理員', permissions: ['*'], effective_permissions: ALL_CODES }),
  makeRole({ code: 'director', name: '主任', effective_permissions: DIRECTOR }),
]

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

async function mountDialog(permissions: string[], props: Record<string, unknown> = {}): Promise<VueWrapper> {
  const { wrapper } = await mountWithApp(RoleCreateDialog, {
    props: { modelValue: true, roles: ROLES, catalog: CATALOG, ...props },
    piniaInitialState: {
      auth: {
        status: 'authenticated',
        user: {
          id: 'u1',
          username: 'hr01',
          display_name: '周人事',
          role: { id: 'r-hr', code: 'hr', name: '人事主管' },
          permissions: [...permissions].sort(),
          must_change_password: false,
        },
      },
    },
  })
  await settle()
  return wrapper as VueWrapper
}

function dialog(): DOMWrapper<Element> {
  const el = Array.from(document.body.querySelectorAll('.el-dialog')).at(-1)
  if (!el) throw new Error('dialog 沒有出現')
  return new DOMWrapper(el)
}

function formItem(label: string): DOMWrapper<Element> {
  const item = dialog()
    .findAll('.el-form-item')
    .find((i) => i.find('.el-form-item__label').text().trim() === label)
  if (!item) throw new Error(`找不到欄位 ${label}`)
  return item
}

async function fill(label: string, value: string): Promise<void> {
  const field = formItem(label).find('input, textarea')
  await field.setValue(value)
  await settle()
}

async function openCopySelect(): Promise<HTMLElement[]> {
  const item = formItem('從既有角色複製權限')
  await item.find('.el-select__wrapper').trigger('click')
  await settle()
  const listId = item.find('input').attributes('aria-controls')
  return Array.from(document.getElementById(listId!)?.querySelectorAll<HTMLElement>('.el-select-dropdown__item') ?? [])
}

async function copyFrom(label: string): Promise<void> {
  const option = (await openCopySelect()).find((o) => o.textContent?.trim() === label)
  if (!option) throw new Error(`找不到複製來源 ${label}`)
  option.click()
  await settle()
}

function permItem(code: string): DOMWrapper<Element> {
  const item = dialog()
    .findAll('.perm-item')
    .find((i) => i.find('.perm-item__code').text() === code)
  if (!item) throw new Error(`找不到權限 ${code}`)
  return item
}

function submitButton(): DOMWrapper<Element> {
  const btn = dialog()
    .findAll('.el-dialog__footer button')
    .find((b) => b.text() === '建立')
  if (!btn) throw new Error('找不到「建立」')
  return btn
}

async function submit(): Promise<void> {
  await submitButton().trigger('click')
  await settle()
}

function postBody(index = 0): Record<string, unknown> {
  const call = mock.history.post[index]
  if (!call) throw new Error(`沒有第 ${index + 1} 個 POST`)
  return JSON.parse(call.data as string) as Record<string, unknown>
}

function createdRole(body: Record<string, unknown>): Role {
  return makeRole({
    id: 'r-new',
    code: body.code as string,
    name: body.name as string,
    is_system: false,
    effective_permissions: body.permissions as string[],
  })
}

function replyCreated(): void {
  mock.onPost('/admin/roles').reply((config) => {
    const body = JSON.parse(config.data as string) as Record<string, unknown>
    return [201, createdRole(body)]
  })
}

describe('RoleCreateDialog', () => {
  beforeEach(() => {
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    document.body.innerHTML = ''
  })

  it('RoleCreateDialog creates role with copied permissions', async () => {
    replyCreated()
    const wrapper = await mountDialog([...TUTOR, ...ROLE_MANAGER])
    expect(dialog().find('.el-dialog__title').text()).toBe('新增角色')
    expect(dialog().attributes('style')).toContain('800px')
    expect(formItem('代碼').text()).toContain('建立後不可修改')

    await fill('代碼', 'senior_tutor')
    await fill('名稱', '資深老師')
    await copyFrom('課輔老師（12 項）')
    expect(dialog().text()).not.toContain('已略過')
    await submit()

    expect(postBody()).toEqual({ code: 'senior_tutor', name: '資深老師', description: null, permissions: TUTOR })
    expect(wrapper.emitted('created')).toEqual([[createdRole(postBody())]])
    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
    expect(document.body.textContent).toContain('角色已建立')
  })

  it('RoleCreateDialog skips ungrantable copied permissions', async () => {
    replyCreated()
    await mountDialog([...CLERK.filter((c) => c !== 'exams:publish'), ...ROLE_MANAGER])

    await fill('代碼', 'senior_clerk')
    await fill('名稱', '資深行政')
    await copyFrom('行政（18 項）')
    expect(formItem('從既有角色複製權限').text()).toContain('已略過 1 項你沒有的權限')
    await submit()

    const permissions = postBody().permissions as string[]
    expect(permissions).not.toContain('exams:publish')
    expect(permissions).toEqual(CLERK.filter((c) => c !== 'exams:publish'))
  })

  it('RoleCreateDialog validates code format and duplicates', async () => {
    mock.onPost('/admin/roles').reply(409, {
      error: { code: 'role_code_taken', message: '角色代碼已存在', details: { code: 'senior_tutor' } },
    })
    const wrapper = await mountDialog([...TUTOR, ...ROLE_MANAGER])

    await fill('代碼', 'Senior-1')
    await fill('名稱', '資深老師')
    await submit()
    await settleErrors()
    expect(formItem('代碼').find('.el-form-item__error').text()).toBe(
      '只能用小寫英文、數字與底線，並以英文字母開頭（2~32 字）',
    )
    expect(mock.history.post).toHaveLength(0)

    await fill('代碼', 'senior_tutor')
    await submit()
    await settleErrors()

    expect(mock.history.post).toHaveLength(1)
    expect(formItem('代碼').find('.el-form-item__error').text()).toBe('代碼已被使用')
    expect(wrapper.emitted('created')).toBeUndefined()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('RoleCreateDialog code length matches backend', async () => {
    replyCreated()
    await mountDialog([...TUTOR, ...ROLE_MANAGER])
    await fill('代碼', `a${'b'.repeat(31)}`)
    await fill('名稱', '資'.repeat(50))
    await submit()

    expect(mock.history.post).toHaveLength(1)
    expect(postBody()).toMatchObject({ code: `a${'b'.repeat(31)}`, name: '資'.repeat(50) })
    document.body.innerHTML = ''

    await mountDialog([...TUTOR, ...ROLE_MANAGER])
    await fill('代碼', `a${'b'.repeat(32)}`)
    await fill('名稱', '資深老師')
    await submit()
    await settleErrors()

    expect(formItem('代碼').find('.el-form-item__error').text()).toContain('2~32 字')
    expect(mock.history.post).toHaveLength(1)
  })

  it('RoleCreateDialog requires code and name', async () => {
    await mountDialog([...TUTOR, ...ROLE_MANAGER])

    await fill('名稱', '   ')
    await submit()
    await settleErrors()

    expect(formItem('代碼').find('.el-form-item__error').text()).toBe('請輸入代碼')
    expect(formItem('名稱').find('.el-form-item__error').text()).toBe('請輸入角色名稱')
    expect(mock.history.post).toHaveLength(0)
  })

  it('RoleCreateDialog sends trimmed name and description', async () => {
    replyCreated()
    await mountDialog([...TUTOR, ...ROLE_MANAGER])

    await fill('代碼', ' weekend_aide ')
    await fill('名稱', ' 週六助理 ')
    await fill('說明', ' 週六上午支援出勤與接送 ')
    await submit()

    expect(postBody()).toEqual({
      code: 'weekend_aide',
      name: '週六助理',
      description: '週六上午支援出勤與接送',
      permissions: [],
    })
  })

  it('RoleCreateDialog lists roles to copy in card order with permission counts', async () => {
    await mountDialog([...TUTOR, ...ROLE_MANAGER])

    const labels = (await openCopySelect()).map((o) => o.textContent?.trim())

    expect(labels).toEqual(['管理員（28 項）', '主任（25 項）', '行政（18 項）', '課輔老師（12 項）', '週六支援老師（2 項）'])
  })

  it('RoleCreateDialog picker only grants own permissions', async () => {
    await mountDialog([...TUTOR, ...ROLE_MANAGER])

    expect(permItem('staff:write').find<HTMLInputElement>('input').element.disabled).toBe(true)
    expect(permItem('exams:write').find<HTMLInputElement>('input').element.disabled).toBe(false)
  })

  it('RoleCreateDialog clearing copy source keeps permissions and hides skip hint', async () => {
    replyCreated()
    await mountDialog([...CLERK.filter((c) => c !== 'exams:publish'), ...ROLE_MANAGER])
    await copyFrom('行政（18 項）')
    expect(formItem('從既有角色複製權限').text()).toContain('已略過 1 項你沒有的權限')

    const copyItem = formItem('從既有角色複製權限')
    await copyItem.find('.el-select').trigger('mouseenter')
    await copyItem.find('.el-select__clear').trigger('click')
    await settle()
    expect(formItem('從既有角色複製權限').text()).not.toContain('已略過')

    await fill('代碼', 'senior_clerk')
    await fill('名稱', '資深行政')
    await submit()
    expect(postBody().permissions).toEqual(CLERK.filter((c) => c !== 'exams:publish'))
  })

  it('RoleCreateDialog shows cannot grant alert with labels', async () => {
    mock.onPost('/admin/roles').reply(403, {
      error: {
        code: 'cannot_grant_permissions',
        message: '不可授出自己沒有的權限',
        details: { permissions: ['exams:publish', 'pickup:override'] },
      },
    })
    const wrapper = await mountDialog([...DIRECTOR, ...ROLE_MANAGER])
    await fill('代碼', 'deputy')
    await fill('名稱', '副主任')
    await copyFrom('主任（25 項）')
    await submit()

    const alert = dialog().find('.el-alert')
    expect(alert.text()).toContain('你沒有以下權限，無法授出：發布成績、代理接送強制完成')
    const form = dialog().find('.el-form').element
    expect(alert.element.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
    expect(wrapper.emitted('created')).toBeUndefined()

    await alert.find('.el-alert__close-btn').trigger('click')
    await settle()
    expect(dialog().find('.el-alert').exists()).toBe(false)
  })

  it('RoleCreateDialog maps validation errors to fields', async () => {
    mock.onPost('/admin/roles').reply(422, {
      error: {
        code: 'validation_error',
        message: '輸入資料格式錯誤',
        details: [{ loc: ['body', 'name'], msg: '名稱最多 50 字', type: 'string_too_long' }],
      },
    })
    await mountDialog([...TUTOR, ...ROLE_MANAGER])
    await fill('代碼', 'senior_tutor')
    await fill('名稱', '資深老師')
    await submit()
    await settleErrors()

    expect(formItem('名稱').find('.el-form-item__error').text()).toBe('名稱最多 50 字')
  })

  it('RoleCreateDialog resets on reopen', async () => {
    const wrapper = await mountDialog([...CLERK.filter((c) => c !== 'exams:publish'), ...ROLE_MANAGER])
    await fill('代碼', 'senior_clerk')
    await copyFrom('行政（18 項）')
    expect(dialog().find('.perm-picker__count').text()).toBe('已選 17 / 28')

    await wrapper.setProps({ modelValue: false })
    await settle()
    await wrapper.setProps({ modelValue: true })
    await settle()

    expect(formItem('代碼').find<HTMLInputElement>('input').element.value).toBe('')
    expect(dialog().find('.perm-picker__count').text()).toBe('已選 0 / 28')
    expect(dialog().text()).not.toContain('已略過')
  })

  it('RoleCreateDialog lays out code and name side by side', async () => {
    await mountDialog([...TUTOR, ...ROLE_MANAGER])

    const row = dialog().find('.role-create__row')
    expect(row.findAll('.el-form-item__label').map((l) => l.text())).toEqual(['代碼', '名稱'])
    // happy-dom 不算版面：鎖住並排與略過提示的橘字
    const style = (roleCreateDialogSource.match(/<style scoped>([\s\S]*?)<\/style>/)?.[1] ?? '').replace(/\s+/g, ' ')
    expect(style).toMatch(/\.role-create__row \{[^}]*grid-template-columns: 1fr 1fr;/)
    expect(style).toMatch(/\.role-create__skipped \{[^}]*color: var\(--el-color-warning-dark-2\);/)
  })
})
