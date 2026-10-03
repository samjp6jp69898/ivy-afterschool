import { flushPromises } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { adminHttp, resetAdminHttpHandlers } from '@/api/http'
import { createApiMock } from '@/test/helpers'
import { useConfirmDelete } from './useConfirmDelete'

interface Subject {
  id: string
  name: string
}

const NATURE: Subject = { id: 's1', name: '自然' }

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
}

async function clickMessageBoxButton(text: string): Promise<void> {
  await settle()
  // 已關閉的訊息框可能仍留在 DOM，取最後（最新）一個
  const button = Array.from(document.body.querySelectorAll<HTMLButtonElement>('.el-message-box button'))
    .filter((b) => b.textContent?.trim() === text)
    .at(-1)
  if (!button) throw new Error(`確認框找不到按鈕「${text}」`)
  button.click()
  await settle()
}

function deleteSubject(row: Subject) {
  return adminHttp.delete(`/admin/subjects/${row.id}`)
}

describe('useConfirmDelete', () => {
  let mock: MockAdapter

  beforeEach(() => {
    resetAdminHttpHandlers()
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    document.body.innerHTML = ''
  })

  it('useConfirmDelete deletes after confirmation', async () => {
    mock.onDelete('/admin/subjects/s1').reply(200, { deleted: true, deactivated: false })
    const { confirmDelete } = useConfirmDelete<Subject>({
      request: deleteSubject,
      confirmMessage: (row) => `確定要刪除 ${row.name} 嗎？`,
    })

    const result = confirmDelete(NATURE)
    await settle()
    expect(document.body.textContent).toContain('確定要刪除 自然 嗎？')
    expect(document.body.querySelector('.el-message-box__title')?.textContent?.trim()).toBe('確認刪除')
    await clickMessageBoxButton('刪除')

    expect(await result).toBe(true)
    expect(mock.history.delete.length).toBe(1)
    await settle()
    expect(document.body.textContent).toContain('已刪除')
  })

  it('useConfirmDelete does nothing when cancelled', async () => {
    mock.onDelete('/admin/subjects/s1').reply(200, {})
    const onSuccess = vi.fn()
    const { confirmDelete } = useConfirmDelete<Subject>({
      request: deleteSubject,
      confirmMessage: (row) => `確定要刪除 ${row.name} 嗎？`,
      onSuccess,
    })

    const result = confirmDelete(NATURE)
    await clickMessageBoxButton('取消')

    expect(await result).toBe(false)
    expect(mock.history.delete.length).toBe(0)
    expect(onSuccess).not.toHaveBeenCalled()
  })

  it('useConfirmDelete shows backend error', async () => {
    mock.onDelete('/admin/roles/r1').reply(409, {
      error: { code: 'role_in_use', message: '仍有 3 位員工使用此角色' },
    })
    const onSuccess = vi.fn()
    const { confirmDelete, deleting } = useConfirmDelete<{ id: string; name: string }>({
      request: (row) => adminHttp.delete(`/admin/roles/${row.id}`),
      confirmMessage: (row) => `確定要刪除 ${row.name} 嗎？`,
      onSuccess,
    })

    const result = confirmDelete({ id: 'r1', name: '課輔老師' })
    await clickMessageBoxButton('刪除')

    expect(await result).toBe(false)
    await settle()
    expect(document.body.textContent).toContain('仍有 3 位員工使用此角色')
    expect(deleting.value).toBe(false)
    expect(onSuccess).not.toHaveBeenCalled()
  })

  it('useConfirmDelete falls back to generic error text', async () => {
    const { confirmDelete } = useConfirmDelete<Subject>({
      request: () => Promise.reject(new Error('boom')),
      confirmMessage: () => '確定要刪除嗎？',
    })

    const result = confirmDelete(NATURE)
    await clickMessageBoxButton('刪除')

    expect(await result).toBe(false)
    await settle()
    expect(document.body.textContent).toContain('刪除失敗')
  })

  it('useConfirmDelete supports dynamic success message', async () => {
    mock.onDelete('/admin/subjects/s1').reply(200, { deleted: false, deactivated: true })
    const { confirmDelete } = useConfirmDelete<Subject>({
      request: (row) => adminHttp.delete(`/admin/subjects/${row.id}`).then((r) => r.data),
      confirmMessage: (row) => `確定要刪除 ${row.name} 嗎？`,
      successMessage: (r) => ((r as { deactivated: boolean }).deactivated ? '已停用（仍被引用）' : '已刪除'),
    })

    const result = confirmDelete(NATURE)
    await clickMessageBoxButton('刪除')

    expect(await result).toBe(true)
    await settle()
    expect(document.body.textContent).toContain('已停用（仍被引用）')
  })

  it('useConfirmDelete calls onSuccess with row and result', async () => {
    mock.onDelete('/admin/subjects/s1').reply(200, { deleted: true, deactivated: false })
    const onSuccess = vi.fn()
    const { confirmDelete } = useConfirmDelete<Subject>({
      request: (row) => adminHttp.delete(`/admin/subjects/${row.id}`).then((r) => r.data),
      confirmMessage: () => '確定要刪除嗎？',
      onSuccess,
    })

    const result = confirmDelete(NATURE)
    await clickMessageBoxButton('刪除')
    await result

    expect(onSuccess).toHaveBeenCalledTimes(1)
    expect(onSuccess).toHaveBeenCalledWith(NATURE, { deleted: true, deactivated: false })
  })

  it('useConfirmDelete ignores repeated calls while busy', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    mock.onDelete('/admin/subjects/s1').reply(async () => {
      await gate
      return [200, { deleted: true, deactivated: false }]
    })
    const { confirmDelete, deleting } = useConfirmDelete<Subject>({
      request: deleteSubject,
      confirmMessage: (row) => `確定要刪除 ${row.name} 嗎？`,
    })

    const first = confirmDelete(NATURE)
    await settle()
    expect(await confirmDelete(NATURE)).toBe(false)
    await settle()
    expect(document.body.querySelectorAll('.el-message-box').length).toBe(1)

    await clickMessageBoxButton('刪除')
    await vi.waitFor(() => expect(mock.history.delete.length).toBe(1))
    expect(deleting.value).toBe(true)
    expect(await confirmDelete(NATURE)).toBe(false)

    release()
    expect(await first).toBe(true)
    expect(mock.history.delete.length).toBe(1)
    expect(deleting.value).toBe(false)
  })

  it('useConfirmDelete uses custom title and button text', async () => {
    const { confirmDelete } = useConfirmDelete<Subject>({
      request: () => Promise.resolve(),
      confirmMessage: (row) => `確定要封存 ${row.name} 嗎？`,
      confirmTitle: '確認封存',
      confirmButtonText: '封存',
    })

    const result = confirmDelete(NATURE)
    await settle()
    expect(document.body.querySelector('.el-message-box__title')?.textContent?.trim()).toBe('確認封存')
    const buttons = Array.from(document.body.querySelectorAll('.el-message-box button')).map((b) => b.textContent?.trim())
    expect(buttons).toContain('封存')
    expect(buttons).toContain('取消')
    await clickMessageBoxButton('取消')
    expect(await result).toBe(false)
  })
})
