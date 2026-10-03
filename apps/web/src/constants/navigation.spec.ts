import * as Icons from '@element-plus/icons-vue'
import { describe, expect, it } from 'vitest'
import { ROUTE_PERMISSIONS } from '@/router/routes'
import { NAVIGATION, visibleNavigation } from './navigation'

const TUTOR_DEFAULT = new Set([
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
  'exams:write',
])

const allItems = () => NAVIGATION.flatMap((g) => g.items)

describe('navigation', () => {
  it('navigation items match route permissions', () => {
    const items = allItems()
    for (const item of items) {
      expect(item.permission, item.path).toBe(ROUTE_PERMISSIONS[item.path])
    }
    const paths = items.map((i) => i.path)
    expect(new Set(paths).size).toBe(paths.length)
  })

  it('navigation menu structure matches spec', () => {
    expect(NAVIGATION.map((g) => [g.key, g.label, g.items.map((i) => i.path)])).toEqual([
      ['home', '首頁', ['/']],
      ['daily', '日常營運', ['/attendance', '/homework', '/pickup', '/pickup/authorizations', '/leaves']],
      ['students', '學生與班級', ['/students', '/classes', '/attendance/monthly']],
      ['exams', '成績', ['/exams']],
      [
        'settings',
        '設定',
        ['/settings/system', '/settings/reference', '/settings/accounts', '/settings/roles', '/settings/audit'],
      ],
    ])
    expect(allItems().map((i) => i.label)).toEqual([
      '今日儀表板',
      '今日出勤',
      '作業進度',
      '接送',
      '代理接送核驗',
      '請假',
      '學生工作台',
      '班級管理',
      '月出勤報表',
      '考試',
      '系統設定',
      '參考資料',
      '員工帳號',
      '角色權限',
      '稽核紀錄',
    ])
  })

  it('navigation icons are element-plus icon names', () => {
    for (const item of allItems()) {
      expect(Object.hasOwn(Icons, item.icon), `${item.path} 的 icon ${item.icon}`).toBe(true)
    }
  })

  it('navigation filters by tutor default permissions', () => {
    expect(visibleNavigation(TUTOR_DEFAULT).map((g) => g.key)).toEqual(['home', 'daily', 'students', 'exams'])
  })

  it('navigation removes empty groups and items', () => {
    const result = visibleNavigation(new Set(['settings:read']))
    expect(result.map((g) => g.key)).toEqual(['settings'])
    expect(result[0]?.items.map((i) => i.path)).toEqual(['/settings/system', '/settings/reference'])
    expect(visibleNavigation(new Set())).toEqual([])
  })

  it('navigation filtering does not mutate the manifest', () => {
    visibleNavigation(new Set(['settings:read']))
    expect(NAVIGATION.find((g) => g.key === 'settings')?.items).toHaveLength(5)
  })
})
