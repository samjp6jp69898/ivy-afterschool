import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import type { StaffMe } from '@/api/auth'
import { NAVIGATION } from '@/constants/navigation'
import { useAuthStore } from '@/stores/auth'
import { mountWithApp } from '@/test/helpers'
import AdminSidebar from './AdminSidebar.vue'
import adminSidebarSource from './AdminSidebar.vue?raw'

// docs/domain_spec.md §3「預設授予」
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
const TUTOR = [...ALL_STAFF, 'exams:write']
const CLERK = [
  ...ALL_STAFF,
  'settings:read',
  'classes:write',
  'students:write',
  'guardians:write',
  'leaves:write',
  'exams:write',
  'exams:publish',
]
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
]

function makeUser(permissions: string[]): StaffMe {
  return {
    id: 'u1',
    username: 'clerk01',
    display_name: '林行政',
    role: { id: 'r1', code: 'clerk', name: '行政' },
    permissions,
    must_change_password: false,
  }
}

async function mountSidebar(
  options: { permissions?: string[]; initialRoute?: string; props?: Record<string, unknown> } = {},
) {
  return mountWithApp(AdminSidebar, {
    props: { collapsed: false, ...options.props },
    initialRoute: options.initialRoute ?? '/students',
    piniaInitialState: {
      auth: { user: makeUser(options.permissions ?? DIRECTOR), status: 'authenticated' },
    },
  })
}

function groupTitles(wrapper: VueWrapper): string[] {
  return wrapper.findAll('.el-menu-item-group__title').map((t) => t.text())
}

function groupItemLabels(wrapper: VueWrapper, title: string): string[] {
  const group = wrapper
    .findAll('.el-menu-item-group')
    .find((g) => g.find('.el-menu-item-group__title').text() === title)
  if (!group) throw new Error(`找不到群組「${title}」，現有：${groupTitles(wrapper).join('、')}`)
  return group.findAll('.el-menu-item').map((i) => i.text())
}

function activeLabels(wrapper: VueWrapper): string[] {
  return wrapper.findAll('.el-menu-item.is-active').map((i) => i.text())
}

function itemByLabel(wrapper: VueWrapper, label: string) {
  const item = wrapper.findAll('.el-menu-item').find((i) => i.text() === label)
  if (!item) throw new Error(`找不到選單項目「${label}」`)
  return item
}

describe('AdminSidebar', () => {
  it('AdminSidebar hides groups without permission', async () => {
    const { wrapper } = await mountSidebar({ permissions: TUTOR })

    const text = wrapper.text()
    expect(text).toContain('今日出勤')
    expect(text).toContain('接送')
    expect(text).toContain('考試')
    expect(text).not.toContain('系統設定')
    expect(text).not.toContain('員工帳號')
    expect(text).not.toContain('稽核紀錄')
    expect(groupTitles(wrapper)).toEqual(['首頁', '日常營運', '學生與班級', '成績'])
  })

  it('AdminSidebar shows settings items per permission', async () => {
    const { wrapper } = await mountSidebar({ permissions: ['settings:read', 'staff:read'] })

    expect(groupTitles(wrapper)).toEqual(['設定'])
    expect(groupItemLabels(wrapper, '設定')).toEqual(['系統設定', '參考資料', '員工帳號'])
  })

  it('AdminSidebar shows every settings item for director and only reference items for clerk', async () => {
    const director = await mountSidebar({ permissions: DIRECTOR })
    expect(groupTitles(director.wrapper)).toEqual(['首頁', '日常營運', '學生與班級', '成績', '設定'])
    expect(groupItemLabels(director.wrapper, '設定')).toEqual([
      '系統設定',
      '參考資料',
      '員工帳號',
      '角色權限',
      '稽核紀錄',
    ])
    expect(groupItemLabels(director.wrapper, '日常營運')).toEqual([
      '今日出勤',
      '作業進度',
      '接送',
      '代理接送核驗',
      '請假',
    ])

    const clerk = await mountSidebar({ permissions: CLERK })
    expect(groupItemLabels(clerk.wrapper, '設定')).toEqual(['系統設定', '參考資料'])
  })

  it('AdminSidebar highlights longest matching prefix', async () => {
    const exams = await mountSidebar({ initialRoute: '/exams/e1' })
    expect(activeLabels(exams.wrapper)).toEqual(['考試'])

    const proxy = await mountSidebar({ initialRoute: '/pickup/authorizations' })
    expect(activeLabels(proxy.wrapper)).toEqual(['代理接送核驗'])
    expect(itemByLabel(proxy.wrapper, '接送').classes()).not.toContain('is-active')
  })

  it('AdminSidebar highlights dashboard only on the root path', async () => {
    const home = await mountSidebar({ initialRoute: '/' })
    expect(activeLabels(home.wrapper)).toEqual(['今日儀表板'])

    const students = await mountSidebar({ initialRoute: '/students' })
    expect(activeLabels(students.wrapper)).toEqual(['學生工作台'])

    // 不在選單上的頁面（/forbidden）沒有任何項目高亮；/examsX 不是 /exams 的分段前綴
    const forbidden = await mountSidebar({ initialRoute: '/forbidden' })
    expect(activeLabels(forbidden.wrapper)).toEqual([])
    const lookalike = await mountSidebar({ initialRoute: '/examsX' })
    expect(activeLabels(lookalike.wrapper)).toEqual([])
  })

  it('AdminSidebar follows route changes after mount', async () => {
    const { wrapper, router } = await mountSidebar({ initialRoute: '/students' })

    await router.push('/attendance/monthly')
    await flushPromises()

    expect(activeLabels(wrapper)).toEqual(['月出勤報表'])
  })

  it('AdminSidebar navigates and emits on click', async () => {
    const { wrapper, router } = await mountSidebar({ initialRoute: '/' })

    await itemByLabel(wrapper, '學生工作台').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/students')
    expect(wrapper.emitted('navigate')?.[0]).toEqual(['/students'])
    expect(activeLabels(wrapper)).toEqual(['學生工作台'])
  })

  it('AdminSidebar empty permissions shows hint', async () => {
    const { wrapper } = await mountSidebar({ permissions: [] })

    expect(wrapper.text()).toContain('沒有可使用的功能，請聯絡管理員')
    expect(wrapper.find('.el-menu').exists()).toBe(false)
    expect(wrapper.findAll('.el-menu-item-group')).toHaveLength(0)
  })

  it('AdminSidebar reacts to permission changes', async () => {
    const { wrapper } = await mountSidebar({ permissions: TUTOR })
    expect(groupTitles(wrapper)).not.toContain('設定')

    useAuthStore().setUser(makeUser([...TUTOR, 'audit:read']))
    await nextTick()

    expect(groupItemLabels(wrapper, '設定')).toEqual(['稽核紀錄'])
  })

  it('AdminSidebar renders an icon for every navigation item', async () => {
    const all = NAVIGATION.flatMap((g) => g.items.map((i) => i.permission))
    const { wrapper } = await mountSidebar({ permissions: all })

    const items = wrapper.findAll('.el-menu-item')
    expect(items).toHaveLength(NAVIGATION.flatMap((g) => g.items).length)
    for (const item of items) {
      expect(item.find('.el-icon svg').exists(), `「${item.text()}」沒有 icon`).toBe(true)
    }
  })

  it('AdminSidebar brand shows fixed system name', async () => {
    const { wrapper } = await mountSidebar()

    const brand = wrapper.find('[data-test=sidebar-brand]')
    expect(brand.text()).toBe('安親班管理系統')
    expect(brand.find('.el-icon svg').exists()).toBe(true)
  })

  it('AdminSidebar collapse button emits toggle', async () => {
    const { wrapper } = await mountSidebar()

    const button = wrapper.find('[aria-label=收合側欄]')
    expect(button.text()).toBe('收合')
    await button.trigger('click')

    expect(wrapper.emitted('toggle')).toHaveLength(1)
    expect(wrapper.emitted('navigate')).toBeUndefined()
  })

  it('AdminSidebar collapsed shows icons only with tooltips', async () => {
    const { wrapper } = await mountSidebar({ props: { collapsed: true } })

    const root = wrapper.find('[data-test=admin-sidebar]')
    expect(root.classes()).toContain('is-collapsed')
    expect(wrapper.find('.el-menu').classes()).toContain('el-menu--collapse')
    // 收合時 el-menu-item 以 tooltip 包住 icon（hover 顯示名稱）
    expect(wrapper.findAll('.el-menu-tooltip__trigger')).toHaveLength(wrapper.findAll('.el-menu-item').length)
    expect(wrapper.find('[data-test=sidebar-brand-text]').isVisible()).toBe(false)

    const button = wrapper.find('[aria-label=展開側欄]')
    expect(button.exists()).toBe(true)
    expect(button.text()).toBe('')
    await button.trigger('click')
    expect(wrapper.emitted('toggle')).toHaveLength(1)

    await wrapper.setProps({ collapsed: false })
    expect(root.classes()).not.toContain('is-collapsed')
    expect(wrapper.find('.el-menu').classes()).not.toContain('el-menu--collapse')
    expect(wrapper.find('[data-test=sidebar-brand-text]').isVisible()).toBe(true)
  })

  it('AdminSidebar drawer mode hides collapse button and ignores collapsed', async () => {
    const { wrapper } = await mountSidebar({ props: { collapsed: true, drawer: true } })

    const root = wrapper.find('[data-test=admin-sidebar]')
    expect(root.classes()).toContain('is-drawer')
    expect(root.classes()).not.toContain('is-collapsed')
    expect(wrapper.find('.el-menu').classes()).not.toContain('el-menu--collapse')
    expect(wrapper.find('[aria-label=收合側欄]').exists()).toBe(false)
    expect(wrapper.find('[aria-label=展開側欄]').exists()).toBe(false)
    expect(wrapper.find('[data-test=sidebar-brand-text]').isVisible()).toBe(true)

    await itemByLabel(wrapper, '請假').trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual(['/leaves'])
  })

  it('AdminSidebar layout rules follow the approved mockup', () => {
    // happy-dom 不計算版面：寬度、項目高度、收合時群組標題改細分隔線以原始碼斷言（真實版面另以瀏覽器確認）
    const style = adminSidebarSource.slice(adminSidebarSource.indexOf('<style'))
    expect(style).toMatch(/\.admin-sidebar\s*\{[^}]*width:\s*220px/)
    expect(style).toMatch(/\.admin-sidebar\.is-collapsed\s*\{[^}]*width:\s*64px/)
    expect(style).toMatch(/\.admin-sidebar\.is-drawer\s*\{[^}]*width:\s*100%/)
    expect(style).toMatch(/\.sidebar__brand\s*\{[^}]*height:\s*56px/)
    expect(style).toMatch(/\.el-menu-item\)\s*\{[^}]*height:\s*44px/)
    expect(style).toMatch(/\.el-menu--collapse\s+\.el-menu-item-group__title\)\s*\{[^}]*height:\s*1px/)
  })
})
