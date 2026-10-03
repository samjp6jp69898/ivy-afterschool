import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { nextTick, reactive, ref } from 'vue'
import { confirmDiscardChanges, useFormDirty } from './useFormDirty'

async function clickDialogButton(text: string): Promise<void> {
  await nextTick()
  await flushPromises()
  const buttons = Array.from(document.body.querySelectorAll('button')).filter(
    (b) => b.textContent?.trim() === text,
  )
  const button = buttons.at(-1)
  if (!button) throw new Error(`找不到按鈕「${text}」`)
  button.click()
}

describe('useFormDirty', () => {
  it('useFormDirty tracks changes against snapshot', async () => {
    const form = reactive({ name: '王小明', tags: ['a'] })
    const { isDirty } = useFormDirty(form)

    expect(isDirty.value).toBe(false)
    form.name = '王大明'
    expect(isDirty.value).toBe(true)
    form.name = '王小明'
    expect(isDirty.value).toBe(false)
    form.tags.push('b')
    expect(isDirty.value).toBe(true)
  })

  it('useFormDirty markClean resets baseline', () => {
    const form = reactive({ name: '王小明', tags: ['a'] })
    const { isDirty, markClean } = useFormDirty(form)

    form.name = '王大明'
    markClean()
    expect(isDirty.value).toBe(false)
    form.name = '王小明'
    expect(isDirty.value).toBe(true)
  })

  it('useFormDirty snapshot is a deep copy, not a shared reference', () => {
    const form = reactive({ guardian: { phone: '0912-000-123' }, tags: ['a'] })
    const { isDirty } = useFormDirty(form)

    form.guardian.phone = '0912-000-456'
    expect(isDirty.value).toBe(true)
    form.guardian.phone = '0912-000-123'
    form.tags.splice(0, 1, 'a')
    expect(isDirty.value).toBe(false)
  })

  it('useFormDirty compares dates by value and arrays by order', () => {
    const form = reactive({ birthday: new Date('2018-05-01T00:00:00Z'), tags: ['a', 'b'] })
    const { isDirty } = useFormDirty(form)

    form.birthday = new Date('2018-05-01T00:00:00Z')
    expect(isDirty.value).toBe(false)
    form.birthday = new Date('2018-05-02T00:00:00Z')
    expect(isDirty.value).toBe(true)
    form.birthday = new Date('2018-05-01T00:00:00Z')
    form.tags = ['b', 'a']
    expect(isDirty.value).toBe(true)
  })

  it('useFormDirty accepts a ref and tracks reassignment', () => {
    const form = ref({ name: '王小明' })
    const { isDirty } = useFormDirty(form)

    form.value = { name: '王小明' }
    expect(isDirty.value).toBe(false)
    form.value = { name: '王大明' }
    expect(isDirty.value).toBe(true)
  })

  it('useFormDirty confirmDiscardChanges resolves by user choice', async () => {
    const discarded = confirmDiscardChanges()
    expect(document.body.textContent).toContain('尚未儲存的變更將會遺失，確定要關閉嗎？')
    await clickDialogButton('放棄變更')
    expect(await discarded).toBe(true)

    const kept = confirmDiscardChanges()
    await clickDialogButton('繼續編輯')
    expect(await kept).toBe(false)
  })
})
