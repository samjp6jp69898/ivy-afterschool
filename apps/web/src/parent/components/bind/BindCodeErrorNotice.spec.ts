import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import BindCodeErrorNotice from './BindCodeErrorNotice.vue'

type Props = { code: string | null; fallbackMessage?: string; orgPhone?: string | null }

function mountNotice(props: Props) {
  return mount(BindCodeErrorNotice, { props })
}

function callLink(wrapper: ReturnType<typeof mountNotice>) {
  return wrapper.findAll('a').find((a) => a.text().includes('聯絡安親班'))
}

const TABLE: [string, string, string, string][] = [
  ['binding_code_invalid', '綁定碼不正確', '請確認是否輸入正確（注意數字 0 與英文 O）', 'error'],
  ['binding_code_expired', '綁定碼已過期', '請向安親班索取新的綁定碼', 'error'],
  ['binding_code_used', '綁定碼已被使用', '每組綁定碼只能使用一次，請向安親班索取新的綁定碼', 'error'],
  ['guardian_already_bound', '這組綁定碼對應的家長已綁定其他 LINE 帳號', '如需更換 LINE 帳號，請聯絡安親班解除綁定', 'error'],
  ['already_bound_to_student', '您已經綁定過這位小孩', '不需要重複綁定', 'info'],
  ['too_many_attempts', '嘗試次數過多', '請稍後再試', 'warning'],
  ['parent_disabled', '您的家長帳號已停用', '請聯絡安親班', 'error'],
]

describe('BindCodeErrorNotice', () => {
  it('BindCodeErrorNotice maps used code', () => {
    const alert = mountNotice({ code: 'binding_code_used' }).get('[role="alert"]')
    expect(alert.text()).toContain('綁定碼已被使用')
    expect(alert.text()).toContain('每組綁定碼只能使用一次，請向安親班索取新的綁定碼')
  })

  it('BindCodeErrorNotice maps already bound guardian', () => {
    const alert = mountNotice({ code: 'guardian_already_bound' }).get('[role="alert"]')
    expect(alert.text()).toContain('這組綁定碼對應的家長已綁定其他 LINE 帳號')
  })

  it.each(TABLE)('BindCodeErrorNotice maps %s to message, next step and tone', (code, message, next, tone) => {
    const alert = mountNotice({ code }).get('[role="alert"]')
    expect(alert.get('.notice__message').text()).toBe(message)
    expect(alert.get('.notice__next').text()).toBe(next)
    expect(alert.text()).toContain('下一步')
    expect(alert.attributes('data-tone')).toBe(tone)
  })

  it('BindCodeErrorNotice falls back', () => {
    const custom = mountNotice({ code: 'weird_code', fallbackMessage: '伺服器忙碌' })
    expect(custom.get('[role="alert"]').text()).toContain('伺服器忙碌')
    expect(custom.get('[role="alert"]').text()).not.toContain('下一步')
    expect(custom.find('.notice__next').exists()).toBe(false)
    const none = mountNotice({ code: null })
    expect(none.get('[role="alert"]').text()).toContain('綁定失敗，請稍後再試')
    expect(none.get('[role="alert"]').text()).not.toContain('下一步')
  })

  it('BindCodeErrorNotice emits retry', async () => {
    const wrapper = mountNotice({ code: 'binding_code_invalid' })
    const retry = wrapper.findAll('button').find((b) => b.text().includes('重新輸入'))
    expect(retry).toBeDefined()
    await retry?.trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
  })

  it('BindCodeErrorNotice keeps buttons outside the alert', () => {
    const wrapper = mountNotice({ code: 'binding_code_expired', orgPhone: '02-2345-6789' })
    expect(wrapper.get('[role="alert"]').find('button, a').exists()).toBe(false)
  })

  it('BindCodeErrorNotice shows call button', () => {
    const expired = mountNotice({ code: 'binding_code_expired', orgPhone: '02-2345-6789' })
    expect(callLink(expired)?.attributes('href')).toBe('tel:0223456789')
    expect(mountNotice({ code: 'binding_code_invalid', orgPhone: '02-2345-6789' }).findAll('a')).toHaveLength(0)
    expect(callLink(mountNotice({ code: 'binding_code_used', orgPhone: null }))).toBeUndefined()
    expect(callLink(mountNotice({ code: 'binding_code_used', orgPhone: '' }))).toBeUndefined()
    expect(callLink(mountNotice({ code: 'parent_disabled', orgPhone: '02-2345-6789' }))).toBeDefined()
    expect(callLink(mountNotice({ code: 'guardian_already_bound', orgPhone: '02-2345-6789' }))).toBeDefined()
    expect(callLink(mountNotice({ code: 'too_many_attempts', orgPhone: '02-2345-6789' }))).toBeUndefined()
  })

  it('BindCodeErrorNotice dials main number before extension', () => {
    const href = (orgPhone: string) =>
      callLink(mountNotice({ code: 'binding_code_expired', orgPhone }))?.attributes('href')
    const hash = callLink(mountNotice({ code: 'binding_code_expired', orgPhone: '02-2345-6789 #12' }))
    expect(hash?.attributes('href')).toBe('tel:0223456789')
    expect(hash?.text()).toContain('02-2345-6789 #12')
    expect(href('02-2345-6789 轉 305')).toBe('tel:0223456789')
    expect(href('02-2345-6789 EXT 7')).toBe('tel:0223456789')
    expect(href('02-2345-6789 分機 12')).toBe('tel:0223456789')
    expect(href('02-2345-6789 x12')).toBe('tel:0223456789')
    expect(href('+886 912-000-123')).toBe('tel:+886912000123')
  })

  it('BindCodeErrorNotice tone by code', () => {
    const tone = (code: string | null) => mountNotice({ code }).get('[role="alert"]').attributes('data-tone')
    expect(tone('already_bound_to_student')).toBe('info')
    expect(tone('too_many_attempts')).toBe('warning')
    expect(tone('binding_code_invalid')).toBe('error')
    expect(tone(null)).toBe('error')
    expect(tone('weird_code')).toBe('error')
  })

  it('BindCodeErrorNotice icon follows tone', () => {
    const icon = (code: string | null) => mountNotice({ code }).get('.notice__icon').text()
    expect(icon('already_bound_to_student')).toBe('info')
    expect(icon('too_many_attempts')).toBe('hourglass_top')
    expect(icon('binding_code_used')).toBe('error')
  })
})
