import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { mountWithApp } from '@/test/helpers'
import SecretInput from './SecretInput.vue'

async function mountSecret(props: { modelValue: string | null; disabled?: boolean }) {
  const { wrapper } = await mountWithApp(SecretInput, { props })
  return wrapper
}

async function clickButton(wrapper: VueWrapper, text: string): Promise<void> {
  const button = wrapper.findAll('button').find((b) => b.text() === text)
  if (!button) throw new Error(`找不到按鈕「${text}」，現有：${wrapper.findAll('button').map((b) => b.text()).join('、')}`)
  await button.trigger('click')
  await flushPromises()
}

async function clickDialogButton(text: string): Promise<void> {
  await nextTick()
  await flushPromises()
  const buttons = Array.from(document.body.querySelectorAll<HTMLButtonElement>('.el-message-box button')).filter(
    (b) => b.textContent?.trim() === text,
  )
  const button = buttons.at(-1)
  if (!button) throw new Error(`確認框找不到按鈕「${text}」`)
  button.click()
  await flushPromises()
}

function lastEmitted(wrapper: VueWrapper): unknown[] | undefined {
  return wrapper.emitted('update:modelValue')?.at(-1)
}

describe('SecretInput', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('SecretInput shows masked state without exposing value in input', async () => {
    const wrapper = await mountSecret({ modelValue: '****abcd' })

    expect(wrapper.text()).toContain('已設定（****abcd）')
    expect(wrapper.findAll('input').length).toBe(0)
    expect(wrapper.findAll('button').map((b) => b.text())).toEqual(['修改', '清除'])
  })

  it('SecretInput edits new value and cancel restores mask', async () => {
    const wrapper = await mountSecret({ modelValue: '****abcd' })

    await clickButton(wrapper, '修改')
    const input = wrapper.find('input')
    expect(input.attributes('type')).toBe('password')
    await input.setValue('new-token-xyz')
    expect(lastEmitted(wrapper)).toEqual(['new-token-xyz'])

    await clickButton(wrapper, '取消')
    expect(lastEmitted(wrapper)).toEqual(['****abcd'])
    expect(wrapper.find('input').exists()).toBe(false)
    expect(wrapper.text()).toContain('已設定（****abcd）')
  })

  it('SecretInput clears after confirmation and can undo', async () => {
    const wrapper = await mountSecret({ modelValue: '****abcd' })

    await clickButton(wrapper, '清除')
    expect(document.body.textContent).toContain('清除後 LINE 推播將無法運作，確定要清除嗎？')
    await clickDialogButton('確定')

    expect(lastEmitted(wrapper)).toEqual([null])
    expect(wrapper.text()).toContain('將於儲存後清除')

    await clickButton(wrapper, '復原')
    expect(lastEmitted(wrapper)).toEqual(['****abcd'])
    expect(wrapper.text()).not.toContain('將於儲存後清除')
    expect(wrapper.text()).toContain('已設定（****abcd）')
  })

  it('SecretInput clear confirm dialog and cancel', async () => {
    const wrapper = await mountSecret({ modelValue: '****abcd' })

    await clickButton(wrapper, '清除')
    await nextTick()
    await flushPromises()
    expect(document.body.querySelector('.el-message-box__title')?.textContent?.trim()).toBe('清除設定值')
    await clickDialogButton('取消')

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(wrapper.findAll('button').map((b) => b.text())).toContain('修改')
  })

  it('SecretInput unset and disabled states', async () => {
    const unset = await mountSecret({ modelValue: null })
    expect(unset.text()).toContain('未設定')
    expect(unset.findAll('button').map((b) => b.text())).toEqual(['設定'])

    const disabled = await mountSecret({ modelValue: '****abcd', disabled: true })
    expect(disabled.text()).toContain('已設定（****abcd）')
    expect(disabled.findAll('button').length).toBe(0)

    const disabledUnset = await mountSecret({ modelValue: null, disabled: true })
    expect(disabledUnset.text()).toContain('未設定')
    expect(disabledUnset.findAll('button').length).toBe(0)
  })

  it('SecretInput cleared state hides undo when disabled', async () => {
    const wrapper = await mountSecret({ modelValue: '****abcd' })
    await clickButton(wrapper, '清除')
    await clickDialogButton('確定')

    await wrapper.setProps({ modelValue: null, disabled: true })

    expect(wrapper.text()).toContain('將於儲存後清除')
    expect(wrapper.findAll('button').length).toBe(0)
  })

  it('SecretInput empty edit keeps initial value', async () => {
    const wrapper = await mountSecret({ modelValue: '****abcd' })

    await clickButton(wrapper, '修改')
    await wrapper.find('input').setValue('new')
    await wrapper.find('input').setValue('')

    expect(lastEmitted(wrapper)).toEqual(['****abcd'])
    expect(wrapper.emitted('update:modelValue')?.some((args) => args[0] === '')).toBe(false)

    const unset = await mountSecret({ modelValue: null })
    await clickButton(unset, '設定')
    await unset.find('input').setValue('abc')
    await unset.find('input').setValue('')
    expect(lastEmitted(unset)).toEqual([null])
  })

  it('SecretInput editor placeholder and autocomplete', async () => {
    const wrapper = await mountSecret({ modelValue: '****abcd' })
    await clickButton(wrapper, '修改')
    const input = wrapper.find('input')
    expect(input.attributes('placeholder')).toBe('輸入新值以取代目前設定')
    expect(input.attributes('autocomplete')).toBe('new-password')
    expect((input.element as HTMLInputElement).value).toBe('')

    const unset = await mountSecret({ modelValue: null })
    await clickButton(unset, '設定')
    expect(unset.find('input').attributes('placeholder')).toBe('輸入設定值')
  })

  it('SecretInput adopts external reset but ignores v-model echo', async () => {
    const wrapper = await mountSecret({ modelValue: '****abcd' })
    await clickButton(wrapper, '修改')
    await wrapper.find('input').setValue('new-token')

    await wrapper.setProps({ modelValue: 'new-token' })
    expect(wrapper.find('input').exists()).toBe(true)

    await wrapper.setProps({ modelValue: '****oken' })
    expect(wrapper.text()).toContain('已設定（****oken）')
    expect(wrapper.find('input').exists()).toBe(false)

    await wrapper.setProps({ modelValue: null })
    expect(wrapper.text()).toContain('未設定')
  })
})
