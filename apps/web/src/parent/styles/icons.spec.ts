// PARENT-193：家長端 Material Symbols 自架子集字型的涵蓋檢查。
// 圖示靠 ligature 渲染，字型子集缺一個名稱就會在畫面上變成被 1em 夾盒裁掉的亂碼，
// 所以 src/parent 與已核可家長端設計稿用到的圖示名稱，都必須列在 icons.css 檔頭的子集清單內。
import { readdirSync, readFileSync } from 'node:fs'
import { basename, dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// vitest 對 *.css?raw 回傳空字串，一律用 readFileSync；不寫成 new URL(相對路徑, import.meta.url)（Vite 會改寫）
const STYLES_DIR = dirname(fileURLToPath(import.meta.url))
const PARENT_DIR = resolve(STYLES_DIR, '..')
const MOCKUPS_DIR = resolve(STYLES_DIR, '../../../../../docs/mockups')
const ICONS_CSS = resolve(STYLES_DIR, 'icons.css')
const FONT_FILE = resolve(PARENT_DIR, 'assets/fonts/material-symbols-rounded-subset.woff2')
const MAX_FONT_BYTES = 60 * 1024

const NAME = '[a-z][a-z0-9_]*'

/** icons.css 檔頭註解最後一段「子集涵蓋的圖示」清單（逗號 / 空白分隔） */
function subsetIcons(): string[] {
  const css = readFileSync(ICONS_CSS, 'utf-8')
  const header = css.slice(0, css.indexOf('*/'))
  const marker = header.indexOf('子集涵蓋的圖示')
  if (marker < 0) throw new Error('icons.css 檔頭找不到「子集涵蓋的圖示」清單')
  const list = header.slice(header.indexOf('\n', marker) + 1)
  return list
    .replace(/^\s*\*/gm, ' ')
    .split(/[,\s]+/)
    .filter(Boolean)
}

function stripComments(source: string): string {
  return source
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|\s)\/\/.*$/gm, '$1')
}

/** 值位置的字串：行首、, : ? | & ( 之後，或接在 => / return 後面；比較（=== 'x'）與鍵不算 */
function valueLiterals(text: string): string[] {
  const pattern = new RegExp(`(?:^|[,:?|&(]|=>|\\breturn)\\s*(['"])(${NAME})\\1`, 'g')
  return Array.from(text.matchAll(pattern), (m) => m[2] as string)
}

/** 從 start（{ 或 [）取到對應的結尾括號 */
function balancedFrom(code: string, start: number): string {
  let depth = 0
  for (let i = start; i < code.length; i++) {
    const ch = code[i]
    if (ch === '{' || ch === '[') depth += 1
    else if (ch === '}' || ch === ']') {
      depth -= 1
      if (depth === 0) return code.slice(start, i + 1)
    }
  }
  return code.slice(start)
}

/**
 * 從原始碼（Vue SFC、.ts 或設計稿 html）掃出用到的 Material Symbols 名稱。只認這幾種寫法：
 *  1. <M3Icon> / <m3-icon> 的 name（靜態，或綁定表達式裡「作為結果」的字串）
 *  2. 名稱以 icon 結尾的屬性：icon="x"、:icon="…"、leading-icon、trailing-icon…
 *  3. 物件鍵 / props 預設值：icon: 'x'、leadingIcon: 'x'
 *  4. 名稱為 XXX_ICONS（全大寫）或 xxxIcons / xxxIconMap 的常數對照表：const LEAVE_TYPE_ICONS = { sick: 'sick' }、[['attendance.', 'how_to_reg']]
 *  5. 同一行宣告、名稱為 icon… 或 xxxIcon 的變數（icon、iconOf、statusIcon）
 *  6. 檔名含 icon 的 .ts 工具檔（例如 notificationEventIcon.ts）裡的值位置字串
 *  7. 直接以 material-symbols-rounded 渲染的文字
 * 其他寫法掃不到：請改成上面幾種，或補掃描規則。
 */
function iconNamesIn(source: string, fileName = ''): Set<string> {
  const code = stripComments(source)
  const found = new Set<string>()
  const add = (names: string[]): void => {
    for (const n of names) found.add(n)
  }

  for (const tag of code.matchAll(/<(?:M3Icon|m3-icon)\b(?:"[^"]*"|'[^']*'|[^>"'])*>/g)) {
    for (const m of tag[0].matchAll(new RegExp(`(?<![:\\w-])name="(${NAME})"`, 'g'))) found.add(m[1] as string)
    for (const m of tag[0].matchAll(/:name="([^"]*)"/g)) add(valueLiterals((m[1] as string).trim()))
  }

  for (const m of code.matchAll(new RegExp(`(?<![\\w:-])[\\w-]*[iI]con="(${NAME})"`, 'g'))) found.add(m[1] as string)
  for (const m of code.matchAll(/(?<!\w):[\w-]*[iI]con="([^"]*)"/g)) add(valueLiterals((m[1] as string).trim()))

  for (const m of code.matchAll(/(?<![\w.$-])[\w$]*[iI]con(?:Name)?\s*:\s*([^\n,}]*)/g)) add(valueLiterals(m[1] as string))

  const tableName = '(?:[A-Z][A-Z0-9_]*ICONS?[A-Z0-9_]*|[a-z][\\w$]*Icons|[a-z][\\w$]*IconMap|iconMap)'
  for (const m of code.matchAll(new RegExp(`\\b(?:const|let|var)\\s+${tableName}\\b[^=\\n]*=\\s*(?=[{[])`, 'g'))) {
    add(valueLiterals(balancedFrom(code, (m.index ?? 0) + m[0].length)))
  }

  for (const m of code.matchAll(/\b(?:const|let|var)\s+(?:icon[\w$]*|[a-z][\w$]*Icons?)\b[^=\n]*=([^\n]*)/g)) add(valueLiterals(m[1] as string))

  if (/icon/i.test(basename(fileName)) && fileName.endsWith('.ts')) add(valueLiterals(code))

  for (const m of code.matchAll(new RegExp(`class="[^"]*material-symbols-rounded[^"]*"[^>]*>\\s*(${NAME})\\s*<`, 'g'))) {
    found.add(m[1] as string)
  }
  return found
}

/** src/parent 下的 .vue / .ts（排除 spec） */
function parentSourceFiles(): string[] {
  return readdirSync(PARENT_DIR, { recursive: true, withFileTypes: true })
    .filter((e) => e.isFile() && /\.(vue|ts)$/.test(e.name) && !e.name.endsWith('.spec.ts'))
    .map((e) => join(e.parentPath, e.name))
}

/** 已核可（preview-status = approved）的家長端設計稿 */
function approvedParentMockups(): string[] {
  return readdirSync(MOCKUPS_DIR)
    .filter((f) => /^parent-.*\.html$/.test(f))
    .map((f) => join(MOCKUPS_DIR, f))
    .filter((file) => /<meta name="preview-status" content="approved">/.test(readFileSync(file, 'utf-8')))
}

/** 名稱 → 用到它的檔案（相對 repo 的路徑） */
function collectIcons(files: string[]): Map<string, string[]> {
  const used = new Map<string, string[]>()
  for (const file of files) {
    for (const name of iconNamesIn(readFileSync(file, 'utf-8'), file)) {
      used.set(name, [...(used.get(name) ?? []), relative(resolve(STYLES_DIR, '../../../../..'), file)])
    }
  }
  return used
}

function missingFrom(used: Map<string, string[]>, subset: Set<string>): string[] {
  return [...used.keys()].filter((name) => !subset.has(name)).sort()
}

function describeMissing(used: Map<string, string[]>, missing: string[]): string {
  return `不在 icons.css 子集清單的圖示：${missing.map((n) => `${n}（${(used.get(n) ?? []).slice(0, 2).join('、')}）`).join('；')}`
}

describe('parent icons subset', () => {
  it('parent icons subset covers every icon used in src/parent', () => {
    const subset = new Set(subsetIcons())
    const used = collectIcons(parentSourceFiles())

    // 掃描本身有效：已知用到的圖示都掃得到（避免規則失效造成空轉）
    for (const known of ['delete', 'expand_more', 'how_to_reg', 'notifications', 'add_a_photo', 'hourglass_top', 'warning']) {
      expect([...used.keys()], `掃不到 ${known}`).toContain(known)
    }
    const missing = missingFrom(used, subset)
    expect(missing, describeMissing(used, missing)).toEqual([])
  })

  it('parent icons subset covers every icon used by approved parent mockups', () => {
    const subset = new Set(subsetIcons())
    const mockups = approvedParentMockups()
    expect(mockups.length).toBeGreaterThan(20)
    const used = collectIcons(mockups)

    for (const known of ['delete', 'sick', 'remove', 'timer_off', 'cancel', 'where_to_vote', 'badge', 'block']) {
      expect([...used.keys()], `掃不到 ${known}`).toContain(known)
    }
    const missing = missingFrom(used, subset)
    expect(missing, describeMissing(used, missing)).toEqual([])
  })

  it('parent icons subset list is sorted and unique', () => {
    const names = subsetIcons()

    expect(names.length).toBeGreaterThan(30)
    for (const name of names) expect(name).toMatch(new RegExp(`^${NAME}$`))
    const duplicates = names.filter((n, i) => names.indexOf(n) !== i)
    expect(duplicates, `重複的圖示：${duplicates.join(', ')}`).toEqual([])
    // Google Fonts 的 icon_names 要求字母序；清單與重抓時帶的參數同序
    expect(names).toEqual([...names].sort())
  })

  it('parent icons subset woff2 has a valid header', () => {
    const font = readFileSync(FONT_FILE)

    expect(font.subarray(0, 4).toString('latin1')).toBe('wOF2')
    // WOFF2 標頭的 length 欄位（第 8~11 byte）等於檔案大小：沒有被截斷或夾帶多餘內容
    expect(font.readUInt32BE(8)).toBe(font.length)
    expect(font.length).toBeGreaterThan(1024)
    expect(font.length).toBeLessThan(MAX_FONT_BYTES)
  })

  it('parent icons subset scanner extracts icon names from markup and tables', () => {
    const names = (source: string, file = ''): string[] => [...iconNamesIn(source, file)].sort()

    // 1. M3Icon 的 name：靜態、綁定常數、三元運算子只取結果值（不取比較用的字串）
    expect(names('<M3Icon name="check" />')).toEqual(['check'])
    expect(names(`<m3-icon :name="request.status === 'cancelled' ? 'cancel' : 'timer_off'"></m3-icon>`)).toEqual([
      'cancel',
      'timer_off',
    ])
    expect(names(`<m3-icon :name="variant === 'offline' ? 'wifi_off' : 'sync_problem'" :size="20"></m3-icon>`)).toEqual([
      'sync_problem',
      'wifi_off',
    ])
    expect(names('<M3Icon :name="icon" />')).toEqual([])
    // 2. 名稱以 icon 結尾的屬性
    expect(names('<M3IconButton icon="delete" label="刪除" />')).toEqual(['delete'])
    expect(names('<m3-list-item leading-icon="tune" trailing-icon="chevron_right"></m3-list-item>')).toEqual([
      'chevron_right',
      'tune',
    ])
    expect(names(`<ParentEmptyState :icon="ok ? 'check_circle' : 'error'" />`)).toEqual(['check_circle', 'error'])
    // 3. 物件鍵 / props 預設值
    expect(names(`const meta = { present: { label: '已到班', icon: 'check_circle' } }`)).toEqual(['check_circle'])
    expect(names(`withDefaults(defineProps<{ icon?: string }>(), { icon: 'inbox' })`)).toEqual(['inbox'])
    // 4. 名稱含 ICON 的常數對照表（物件與 tuple 陣列）
    expect(names(`const LEAVE_TYPE_ICONS = { sick: 'sick', personal: 'event_busy', other: 'event_note' }`)).toEqual([
      'event_busy',
      'event_note',
      'sick',
    ])
    expect(names(`const EVENT_ICONS: [prefix: string, icon: string][] = [['attendance.', 'how_to_reg'], ['pickup.', 'directions_walk']]`)).toEqual([
      'directions_walk',
      'how_to_reg',
    ])
    // 6. 檔名含 icon 的 .ts 工具檔：fallback 也算
    expect(names(`export const f = (e: string) => table.find(([p]) => e.startsWith(p))?.[1] ?? 'notifications'`, 'notificationEventIcon.ts')).toEqual(['notifications'])
    expect(names(`export const f = () => 'notifications'`, 'other.ts')).toEqual([])
    // 7. 直接用 material-symbols-rounded 渲染
    expect(names('<span class="material-symbols-rounded">delete</span>')).toEqual(['delete'])
    // 名稱含 Icon 的元件物件（M3Icon、M3IconButton）與 CSS 規則不是對照表：裡面的 'click'、'standard'、'liga' 不算圖示
    expect(
      names(`const M3IconButton = { props: { variant: { type: String, default: 'standard' } }, template: '<button @click="$emit(\\'click\\')">' }`),
    ).toEqual([])
    expect(names(`.m3-nav-tab__icon::before { content: ''; font-feature-settings: 'liga'; }`)).toEqual([])
    // 一般字串、註解與狀態值不算圖示
    expect(names(`/* icon: 'ghost' */\n// icon="ghost"\nconst status = 'cancelled'\nemit('update:modelValue')`)).toEqual([])
    expect(names('<!-- <M3Icon name="ghost" /> --><button class="m3-icon-button" type="button"></button>')).toEqual([])
  })

  it('parent icons subset scanner recognizes icon table names', () => {
    const names = (source: string): string[] => [...iconNamesIn(source)].sort()

    // 全大寫常數：ICON 可以在開頭（ICONS、ICON_MAP），型別註記可有可無
    expect(names(`const ICONS: Record<Tone, string> = { error: 'error', warning: 'hourglass_top', info: 'info' }`)).toEqual([
      'error',
      'hourglass_top',
      'info',
    ])
    expect(names(`const ICON_MAP = { sick: 'sick', other: 'event_note' }`)).toEqual(['event_note', 'sick'])
    expect(names(`const ICON_MAP: Record<string, () => string> = { done: 'check' }`)).toEqual(['check'])
    expect(names(`export const FOO_ICONS = { a: 'add' }`)).toEqual(['add'])
    expect(names(`const XXX_ICON_MAP = { a: 'remove' }`)).toEqual(['remove'])
    expect(names(`const TONE_ICON = { a: 'info' }`)).toEqual(['info'])
    expect(names(`const KIND_TO_ICON = { a: 'link' }`)).toEqual(['link'])
    expect(names(`const ICON_BY_KIND = { a: 'pin' }`)).toEqual(['pin'])
    expect(names(`const ICONS_BY_STATUS = { a: 'today' }`)).toEqual(['today'])
    // 多行物件、Object.freeze 包裝、camelCase 名稱
    expect(names(`const ICONS = {\n  error: 'error',\n  info: 'info',\n}`)).toEqual(['error', 'info'])
    expect(names(`const ICONS = Object.freeze({ a: 'help' })`)).toEqual(['help'])
    expect(names(`const icons = { a: 'edit' }`)).toEqual(['edit'])
    expect(names(`const iconMap = { a: 'close' }`)).toEqual(['close'])
    expect(names(`const statusIcons = { a: 'schedule' }`)).toEqual(['schedule'])
    expect(names(`const eventIconMap: Record<string, string> = { a: 'send' }`)).toEqual(['send'])
    // 圖示的其他屬性（尺寸、顏色、class）不是圖示名稱對照表：裡面的 'small'、'green' 不算
    expect(names(`const ICON_SIZES = { sm: 'small', lg: 'large' }`)).toEqual([])
    expect(names(`const ICON_COLORS = { ok: 'green' }`)).toEqual([])
    expect(names(`const FOO_ICON_SIZES = { sm: 'small' }`)).toEqual([])
    expect(names(`const ICON_SIZE = { sm: 'small' }`)).toEqual([])
    expect(names(`const iconSizes = { sm: 'small' }`)).toEqual([])
    expect(names(`const iconSize = 'small'`)).toEqual([])
    expect(names(`const iconColor = dark ? 'primary' : 'secondary'`)).toEqual([])
    // 不是物件 / 陣列字面值的右式不掃（避免把函式呼叫裡的字串當圖示）
    expect(names(`const ICONS = buildIcons('legacy')`)).toEqual([])
  })
})
