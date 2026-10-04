import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import StatusPill from './StatusPill.vue'
import pillSource from './StatusPill.vue?raw'

// vitest 的 css ?raw 會回傳空字串，改用 fs 讀原始檔
const tokens = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), '../styles/m3-tokens.css'),
  'utf-8',
)

/** 取出某個選擇器開頭的第一個樣式區塊內容 */
function block(css: string, selector: string): string {
  const start = css.indexOf(`${selector} {`)
  expect(start, `找不到 ${selector} 區塊`).toBeGreaterThanOrEqual(0)
  return css.slice(start, css.indexOf('\n}', start))
}

describe('StatusPill', () => {
  it('StatusPill renders label and tone class', () => {
    const wrapper = mount(StatusPill, { props: { label: '已到班', tone: 'success' } })
    expect(wrapper.text()).toBe('已到班')
    expect(wrapper.classes()).toContain('status-pill--success')
    for (const tone of ['warning', 'danger', 'info', 'neutral'] as const) {
      expect(mount(StatusPill, { props: { label: 'x', tone } }).classes()).toContain(`status-pill--${tone}`)
    }
  })

  it('StatusPill defaults neutral and optional icon', () => {
    const plain = mount(StatusPill, { props: { label: '未開始' } })
    expect(plain.classes()).toContain('status-pill--neutral')
    expect(plain.find('.m3-icon').exists()).toBe(false)
    expect(plain.classes()).not.toContain('has-icon')

    const withIcon = mount(StatusPill, { props: { label: '未開始', icon: 'schedule' } })
    expect(withIcon.get('.m3-icon').text()).toBe('schedule')
    expect(withIcon.get('.m3-icon').attributes('aria-hidden')).toBe('true')
    expect(withIcon.classes()).toContain('has-icon')
    expect(withIcon.text()).toContain('未開始')
  })

  it('StatusPill is a non interactive span with no role', () => {
    const wrapper = mount(StatusPill, { props: { label: '已到班', tone: 'success', icon: 'check_circle' } })
    expect(wrapper.element.tagName).toBe('SPAN')
    expect(wrapper.attributes('role')).toBeUndefined()
    expect(wrapper.attributes('tabindex')).toBeUndefined()
  })

  it('StatusPill warning uses warning tokens defined in both themes', () => {
    const warning = block(pillSource, '.status-pill--warning')
    expect(warning).toContain('var(--m3-warning-container)')
    expect(warning).toContain('var(--m3-on-warning-container)')
    expect(pillSource).not.toContain('tertiary')

    const light = block(tokens, ':root')
    expect(light).toContain('--m3-warning-container: #ffddb0;')
    expect(light).toContain('--m3-on-warning-container: #2b1700;')
    const dark = block(tokens, ":root[data-theme='dark']")
    expect(dark).toContain('--m3-warning-container: #5b3a00;')
    expect(dark).toContain('--m3-on-warning-container: #ffddb0;')
  })

  it('StatusPill maps other tones to container tokens', () => {
    const expected: Record<string, [string, string]> = {
      success: ['primary-container', 'on-primary-container'],
      danger: ['error-container', 'on-error-container'],
      info: ['secondary-container', 'on-secondary-container'],
      neutral: ['surface-container-high', 'on-surface'],
    }
    for (const [tone, [bg, fg]] of Object.entries(expected)) {
      const rule = block(pillSource, `.status-pill--${tone}`)
      expect(rule).toContain(`background: var(--m3-${bg})`)
      expect(rule).toContain(`color: var(--m3-${fg})`)
    }
  })
})
