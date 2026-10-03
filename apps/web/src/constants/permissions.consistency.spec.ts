/// <reference types="node" />
// FRONTEND-008：前端權限碼與後端 Permission enum（apps/api/app/core/permissions.py）一致性測試。
import { existsSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { ALL_PERMISSION_CODES, isPermissionCode, PERMISSIONS } from './permissions'

// 不寫成 new URL(相對路徑, import.meta.url)：Vite 會把這個字面模式改寫成 asset URL（http://localhost/...）
const HERE = dirname(fileURLToPath(import.meta.url))
const BACKEND_PERMISSIONS_PATH = resolve(HERE, '../../../api/app/core/permissions.py')

/** 擷取 `class Permission(StrEnum):` 區塊內所有 `= "xxx:yyy"` 字串值；檔案不存在直接 throw（不可 skip）。 */
function readBackendPermissions(path: string): Set<string> {
  if (!existsSync(path)) {
    throw new Error(`找不到後端權限碼檔案：${path}`)
  }
  const lines = readFileSync(path, 'utf8').split('\n')
  const start = lines.findIndex((line) => line.startsWith('class Permission(StrEnum):'))
  if (start < 0) {
    throw new Error(`${path} 中找不到 class Permission(StrEnum):`)
  }
  const codes = new Set<string>()
  for (const line of lines.slice(start + 1)) {
    // 遇到下一個頂層敘述（非空白、非縮排）即為區塊結束
    if (line.trim() !== '' && !/^\s/.test(line)) break
    const m = line.match(/=\s*"([^"]+)"/)
    if (m?.[1]) codes.add(m[1])
  }
  return codes
}

describe('permissions constants', () => {
  it('permissions constants match backend Permission enum', () => {
    const backend = readBackendPermissions(BACKEND_PERMISSIONS_PATH)
    const frontend = new Set<string>(ALL_PERMISSION_CODES)

    expect(backend.size).toBe(28)
    expect(frontend.size).toBe(28)
    expect(ALL_PERMISSION_CODES).toHaveLength(28)
    expect([...frontend].sort()).toEqual([...backend].sort())
    expect(frontend.has('students:purge')).toBe(true)
  })

  it('permissions constants ALL_PERMISSION_CODES lists every PERMISSIONS value', () => {
    expect([...ALL_PERMISSION_CODES].sort()).toEqual(Object.values(PERMISSIONS).sort())
  })

  it('permissions constants reject wildcard and unknown', () => {
    expect(isPermissionCode('*')).toBe(false)
    expect(isPermissionCode('students:delete')).toBe(false)
    expect(isPermissionCode('pickup:override')).toBe(true)
  })

  it('permissions constants fail loudly when backend file missing', () => {
    const missing = resolve(HERE, '../../../api/app/core/no_such_permissions.py')

    expect(() => readBackendPermissions(missing)).toThrow(missing)
  })
})
