import type { DOMWrapper, VueWrapper } from '@vue/test-utils'
import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import type { PermissionCatalog } from '@/api/roles'
import { mountWithApp } from '@/test/helpers'
import PermissionPicker from './PermissionPicker.vue'

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

async function mountPicker(props: Record<string, unknown>): Promise<VueWrapper> {
  const { wrapper } = await mountWithApp(PermissionPicker, {
    props: { catalog: CATALOG, modelValue: [], ...props },
  })
  return wrapper as VueWrapper
}

function group(wrapper: VueWrapper, label: string): DOMWrapper<Element> {
  const g = wrapper.findAll('.perm-group').find((el) => el.find('.perm-group__head .el-checkbox').text() === label)
  if (!g) throw new Error(`找不到權限組 ${label}`)
  return g
}

function item(wrapper: VueWrapper, code: string): DOMWrapper<Element> {
  const el = wrapper.findAll('.perm-item').find((i) => i.find('.perm-item__code').text() === code)
  if (!el) throw new Error(`找不到權限 ${code}`)
  return el
}

function checkboxInput(el: DOMWrapper<Element>): DOMWrapper<HTMLInputElement> {
  return el.find<HTMLInputElement>('input[type=checkbox]')
}

function lastEmitted(wrapper: VueWrapper): string[] {
  const all = wrapper.emitted('update:modelValue') ?? []
  const last = all.at(-1)
  if (!last) throw new Error('沒有 emit update:modelValue')
  return last[0] as string[]
}

describe('PermissionPicker', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('PermissionPicker groups permissions with counts', async () => {
    const wrapper = await mountPicker({ modelValue: ['students:read', 'students:write'] })

    expect(wrapper.findAll('.perm-group')).toHaveLength(9)
    expect(group(wrapper, '班級與學生').find('.perm-group__count').text()).toBe('已選 2 / 7')
    expect(group(wrapper, '接送').find('.perm-group__count').text()).toBe('已選 0 / 3')
    expect(wrapper.find('.perm-picker__count').text()).toBe('已選 2 / 28')
    expect(item(wrapper, 'students:read').find('.perm-item__label').text()).toBe('查看學生')

    const headCheckbox = group(wrapper, '班級與學生').find('.perm-group__head .el-checkbox')
    expect(headCheckbox.find('.el-checkbox__input').classes()).toContain('is-indeterminate')
  })

  it('PermissionPicker emits sorted selection', async () => {
    const modelValue = ['students:read', 'students:write']
    const wrapper = await mountPicker({ modelValue })

    await checkboxInput(item(wrapper, 'guardians:write')).setValue(true)

    expect(wrapper.emitted('update:modelValue')?.[0]?.[0]).toEqual([
      'guardians:write',
      'students:read',
      'students:write',
    ])
    expect(modelValue).toEqual(['students:read', 'students:write'])
  })

  it('PermissionPicker disables ungrantable additions but allows removal', async () => {
    const grantable = new Set(['staff:read', 'students:read'])
    const wrapper = await mountPicker({ modelValue: ['staff:read', 'audit:read'], grantable })

    expect(checkboxInput(item(wrapper, 'staff:write')).attributes('disabled')).toBeDefined()
    expect(item(wrapper, 'staff:write').find('.el-checkbox').classes()).toContain('is-disabled')
    expect(checkboxInput(item(wrapper, 'audit:read')).attributes('disabled')).toBeUndefined()

    await checkboxInput(item(wrapper, 'audit:read')).setValue(false)

    expect(lastEmitted(wrapper)).toEqual(['staff:read'])
  })

  it('PermissionPicker select-all only grantable codes', async () => {
    const grantable = new Set(['pickup:read', 'pickup:operate'])
    const wrapper = await mountPicker({ modelValue: ['exams:read'], grantable })

    await checkboxInput(group(wrapper, '接送').find('.perm-group__head')).setValue(true)

    const emitted = lastEmitted(wrapper)
    expect(emitted).toContain('pickup:read')
    expect(emitted).toContain('pickup:operate')
    expect(emitted).not.toContain('pickup:override')
    expect(emitted).toContain('exams:read')
  })

  it('PermissionPicker group uncheck removes all and disables when nothing operable', async () => {
    const grantable = new Set(['pickup:read'])
    const wrapper = await mountPicker({
      modelValue: ['pickup:read', 'pickup:operate', 'pickup:override'],
      grantable,
    })

    await checkboxInput(group(wrapper, '接送').find('.perm-group__head')).setValue(false)
    expect(lastEmitted(wrapper)).toEqual([])

    // 考試成績組沒有任何已勾選、也沒有可授出的碼
    expect(checkboxInput(group(wrapper, '考試成績').find('.perm-group__head')).attributes('disabled')).toBeDefined()
  })

  it('PermissionPicker search filters and empty result', async () => {
    const wrapper = await mountPicker({})
    const search = wrapper.find('.perm-picker__search input')

    await search.setValue('接送')
    expect(wrapper.findAll('.perm-group').map((g) => g.find('.perm-group__head .el-checkbox').text())).toEqual([
      '接送',
    ])

    await search.setValue('EXAMS:PUB')
    expect(wrapper.findAll('.perm-item__code').map((c) => c.text())).toEqual(['exams:publish'])

    await search.setValue('zzz')
    expect(wrapper.findAll('.perm-group')).toHaveLength(0)
    expect(wrapper.text()).toContain('找不到符合的權限')
  })

  it('PermissionPicker collapses groups', async () => {
    const wrapper = await mountPicker({})

    await group(wrapper, '出勤').find('.perm-group__toggle').trigger('click')

    expect(group(wrapper, '出勤').find('.perm-group__body').exists()).toBe(false)
    expect(group(wrapper, '請假').find('.perm-group__body').exists()).toBe(true)
  })

  it('PermissionPicker highlights role defaults and disables everything', async () => {
    const wrapper = await mountPicker({
      modelValue: ['exams:read'],
      highlight: new Set(['exams:read']),
      disabled: true,
    })

    expect(item(wrapper, 'exams:read').text()).toContain('角色預設')
    expect(item(wrapper, 'exams:write').text()).not.toContain('角色預設')
    for (const input of wrapper.findAll('.perm-group input[type=checkbox]')) {
      expect(input.attributes('disabled')).toBeDefined()
    }
    await flushPromises()
  })
})
