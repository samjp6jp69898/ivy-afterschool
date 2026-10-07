import { mount, type VueWrapper } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { ParentPickupRequest } from '../../api/pickupRequests'
import PickupReplyMessage from './PickupReplyMessage.vue'
import source from './PickupReplyMessage.vue?raw'

type ReplyRequest = Pick<ParentPickupRequest, 'reply_ready_eta' | 'reply_message' | 'reply_source'> & {
  replied_at?: string | null
}

const BUBBLE = '[data-testid="reply-bubble"]'
const WAITING_TEXT = '已通知老師，稍後回覆預計時間'

function request(overrides: Partial<ReplyRequest> = {}): ReplyRequest {
  return { reply_ready_eta: null, reply_message: null, reply_source: null, replied_at: null, ...overrides }
}

function mountReply(overrides: Partial<ReplyRequest> = {}, compact = false) {
  return mount(PickupReplyMessage, { props: { request: request(overrides), compact } })
}

/** 根元素的直接子節點（版面由上而下的區塊）以第一個 class 表示 */
function blocks(wrapper: VueWrapper): (string | undefined)[] {
  const root = wrapper.element as Element
  return Array.from(root.children).map((el) => el.classList[0])
}

/** ?raw 原始碼中某選擇器（行首，可縮排）的所有樣式區塊內容，依出現順序 */
function rules(selector: string): string[] {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return [...source.matchAll(new RegExp(`^\\s*${escaped} \\{([^}]*)\\}`, 'gm'))].map((m) => m[1] ?? '')
}

function rule(selector: string): string {
  const [first] = rules(selector)
  expect(first, `找不到 ${selector} 區塊`).toBeDefined()
  return first ?? ''
}

describe('PickupReplyMessage', () => {
  it('PickupReplyMessage shows eta message and time', () => {
    const wrapper = mountReply({
      reply_ready_eta: '17:30',
      reply_message: '數學還在訂正，預計 17:30 可接送',
      replied_at: '2026-10-02T09:01:00Z',
      reply_source: 'staff',
    })

    expect(wrapper.attributes('aria-live')).toBe('polite')
    expect(wrapper.text()).toContain('預計 17:30 可接送')
    expect(wrapper.get('.prm-eta').text()).toBe('預計 17:30 可接送')
    expect(wrapper.get(BUBBLE).text()).toBe('數學還在訂正，預計 17:30 可接送')
    expect(wrapper.get('.prm-time').text()).toBe('17:01 回覆')
    expect(wrapper.get('.prm-source').text()).toContain('老師回覆')
    expect(wrapper.text()).not.toContain('系統自動回覆')
  })

  it('PickupReplyMessage waiting state', () => {
    const wrapper = mountReply()

    expect(wrapper.text()).toContain(WAITING_TEXT)
    expect(wrapper.get('.prm-wait .m3-icon').text()).toBe('hourglass_top')
    const bar = wrapper.get('[role="progressbar"]')
    expect(bar.attributes('aria-label')).toBe('等待老師回覆')
    expect(wrapper.find(BUBBLE).exists()).toBe(false)
    expect(wrapper.find('.prm-eta').exists()).toBe(false)
    expect(wrapper.find('.prm-source').exists()).toBe(false)
    expect(wrapper.find('.prm-time').exists()).toBe(false)
    expect(wrapper.attributes('aria-live')).toBe('polite')
  })

  it('PickupReplyMessage waiting state hides source and time even when they are set', () => {
    const wrapper = mountReply({ reply_source: 'staff', replied_at: '2026-10-02T09:01:00Z' })

    expect(wrapper.text()).toContain(WAITING_TEXT)
    expect(wrapper.text()).not.toContain('老師回覆')
    expect(wrapper.text()).not.toContain('17:01')
  })

  it('PickupReplyMessage escapes html', () => {
    const wrapper = mountReply({ reply_message: '<b>x</b>', reply_source: 'staff' })

    expect(wrapper.text()).toContain('<b>x</b>')
    expect(wrapper.get(BUBBLE).text()).toBe('<b>x</b>')
    expect(wrapper.find('b').exists()).toBe(false)

    const link = mountReply({ reply_message: '請由<a href="#">側門</a>進入', reply_source: 'staff' })
    expect(link.get(BUBBLE).text()).toBe('請由<a href="#">側門</a>進入')
    expect(link.find('a').exists()).toBe(false)
    // 範本只用文字插值，不用 v-html（說明註解在 script 區，不算）
    expect(source.slice(source.indexOf('<template>'), source.indexOf('<style'))).not.toContain('v-html')
  })

  it('PickupReplyMessage compact one line', () => {
    const wrapper = mountReply(
      {
        reply_message: '作業已完成，可以接送\n請由側門進入',
        reply_source: 'staff',
        replied_at: '2026-10-02T09:01:00Z',
      },
      true,
    )

    expect(wrapper.text()).toContain('作業已完成，可以接送')
    expect(wrapper.text()).not.toContain('請由側門進入')
    expect(wrapper.text()).not.toContain('17:01')
    expect(wrapper.text()).not.toMatch(/\d\d:\d\d\s*回覆/)
    expect(wrapper.find('.prm-time').exists()).toBe(false)
    expect(wrapper.find(BUBBLE).exists()).toBe(false)
    expect(wrapper.get('.prm-compact__text').text()).toBe('作業已完成，可以接送')
    expect(wrapper.get('.prm-source').text()).toContain('老師回覆')
  })

  it('PickupReplyMessage labels auto reply', () => {
    const auto = mountReply({ reply_source: 'auto', reply_message: '作業已完成，可以接送' })
    expect(auto.text()).toContain('系統自動回覆')
    expect(auto.get('.prm-source .m3-icon').text()).toBe('smart_toy')
    expect(auto.get('.prm-source .m3-icon').attributes('aria-hidden')).toBe('true')
    expect(auto.text()).not.toContain('老師回覆')

    const staff = mountReply({ reply_source: 'staff', reply_message: '馬上下來' })
    expect(staff.text()).toContain('老師回覆')
    expect(staff.get('.prm-source .m3-icon').text()).toBe('person')
    expect(staff.text()).not.toContain('系統自動回覆')

    const none = mountReply({ reply_source: null })
    expect(none.text()).not.toContain('系統自動回覆')
    expect(none.text()).not.toContain('老師回覆')
  })

  it('PickupReplyMessage drops duplicated eta line', () => {
    const wrapper = mountReply({
      reply_ready_eta: '17:30',
      reply_message: '預計 17:30 可接送\n剩數學訂正',
      reply_source: 'auto',
    })

    expect(wrapper.text().split('預計 17:30 可接送')).toHaveLength(2)
    expect(wrapper.get(BUBBLE).text()).toBe('剩數學訂正')

    const only = mountReply({
      reply_ready_eta: '17:30',
      reply_message: '預計 17:30 可接送',
      reply_source: 'auto',
    })
    expect(only.find(BUBBLE).exists()).toBe(false)
    expect(only.get('.prm-eta').text()).toBe('預計 17:30 可接送')
    expect(only.text().split('預計 17:30 可接送')).toHaveLength(2)
  })

  it('PickupReplyMessage keeps the whole message unless its first line equals the eta line', () => {
    // 首行的時間與 reply_ready_eta 不同 → 不是重複行
    const otherTime = mountReply({
      reply_ready_eta: '17:30',
      reply_message: '預計 17:40 可接送\n剩數學訂正',
      reply_source: 'staff',
    })
    expect(otherTime.get(BUBBLE).element.textContent).toBe('預計 17:40 可接送\n剩數學訂正')

    // 沒有 ETA → 訊息照原樣顯示
    const noEta = mountReply({ reply_message: '預計 17:30 可接送\n剩數學訂正', reply_source: 'staff' })
    expect(noEta.find('.prm-eta').exists()).toBe(false)
    expect(noEta.get(BUBBLE).element.textContent).toBe('預計 17:30 可接送\n剩數學訂正')

    // 只有首行相同才省略：同一句出現在第二行不動
    const secondLine = mountReply({
      reply_ready_eta: '17:30',
      reply_message: '作業還在訂正\n預計 17:30 可接送',
      reply_source: 'staff',
    })
    expect(secondLine.get(BUBBLE).element.textContent).toBe('作業還在訂正\n預計 17:30 可接送')
  })

  it('PickupReplyMessage trims the duplicated line and the remaining text', () => {
    const wrapper = mountReply({
      reply_ready_eta: '17:30',
      reply_message: '  預計 17:30 可接送  \n\n  剩數學訂正\n請由側門進入  ',
      reply_source: 'auto',
    })

    expect(wrapper.get(BUBBLE).element.textContent).toBe('剩數學訂正\n請由側門進入')

    const blankRest = mountReply({
      reply_ready_eta: '17:30',
      reply_message: '預計 17:30 可接送\n  \n',
      reply_source: 'auto',
    })
    expect(blankRest.find(BUBBLE).exists()).toBe(false)
  })

  it('PickupReplyMessage renders blocks top to bottom and only those with values', () => {
    const full = mountReply({
      reply_ready_eta: '17:30',
      reply_message: '數學訂正中',
      reply_source: 'staff',
      replied_at: '2026-10-02T09:01:00Z',
    })
    expect(blocks(full)).toEqual(['prm-source', 'prm-eta', 'prm-bubble', 'prm-time'])

    // 只有 ETA：沒有泡泡，回覆時間跟在 ETA 下方
    const etaOnly = mountReply({ reply_ready_eta: '18:00', reply_source: 'staff', replied_at: '2026-10-02T09:12:00Z' })
    expect(blocks(etaOnly)).toEqual(['prm-source', 'prm-eta', 'prm-time'])
    expect(etaOnly.get('.prm-time').text()).toBe('17:12 回覆')

    // 只有訊息：沒有 ETA 大字
    const messageOnly = mountReply({ reply_message: '小明在洗手，馬上下來', reply_source: 'staff', replied_at: '2026-10-02T09:20:00Z' })
    expect(blocks(messageOnly)).toEqual(['prm-source', 'prm-bubble', 'prm-time'])

    // 沒有 replied_at：不畫時間（含 undefined）
    const noTime = mountReply({ reply_ready_eta: '17:30', reply_source: 'auto', replied_at: null })
    expect(blocks(noTime)).toEqual(['prm-source', 'prm-eta'])
    const undefinedTime = mount(PickupReplyMessage, {
      props: { request: { reply_ready_eta: '17:30', reply_message: null, reply_source: 'auto' } },
    })
    expect(blocks(undefinedTime)).toEqual(['prm-source', 'prm-eta'])
  })

  it('PickupReplyMessage converts replied_at to taipei clock', () => {
    // UTC 10/01 16:30 = 台北 10/02 00:30
    const wrapper = mountReply({ reply_message: '馬上下來', reply_source: 'staff', replied_at: '2026-10-01T16:30:00Z' })

    expect(wrapper.get('.prm-time').text()).toBe('00:30 回覆')
  })

  it('PickupReplyMessage eta line is one continuous text with the time emphasised', () => {
    const wrapper = mountReply({ reply_ready_eta: '17:30', reply_source: 'staff' })
    const eta = wrapper.get('.prm-eta')

    expect(eta.element.textContent?.replace(/\s+/g, ' ').trim()).toBe('預計 17:30 可接送')
    expect(eta.get('.prm-eta__time').text()).toBe('17:30')
    expect(eta.classes()).toContain('m3-headline-small')

    const time = rule('.prm-eta__time')
    expect(time).toContain('color: var(--m3-primary)')
    expect(time).toMatch(/font-weight:\s*500/)
    expect(time).toContain('font-variant-numeric: tabular-nums')
  })

  it('PickupReplyMessage source label sits above and uses label-medium on-surface-variant', () => {
    const wrapper = mountReply({ reply_message: 'x', reply_source: 'auto' })

    expect(wrapper.get('.prm-source').classes()).toContain('m3-label-medium')
    expect(rule('.prm-source')).toContain('color: var(--m3-on-surface-variant)')
    expect(wrapper.get('.prm-source .m3-icon').attributes('style')).toContain('font-size: 16px')
  })

  it('PickupReplyMessage bubble keeps line breaks and shows long text in full', () => {
    const long = `${'王小明今天數學習作還在訂正，'.repeat(8)}\n老師會陪他做完再下課。\n\n請由正門進入並在櫃台簽名。`
    const wrapper = mountReply({ reply_message: long, reply_source: 'staff' })
    const bubble = wrapper.get(BUBBLE)

    expect(bubble.element.textContent).toBe(long)
    expect(bubble.element.textContent).toContain('\n\n')
    expect(bubble.classes()).toContain('m3-body-large')

    const style = rule('.prm-bubble')
    expect(style).toContain('white-space: pre-wrap')
    expect(style).toContain('overflow-wrap: anywhere')
    expect(style).toContain('background: var(--m3-secondary-container)')
    expect(style).toContain('color: var(--m3-on-secondary-container)')
    expect(style).toContain('padding: 12px 16px')
    expect(style).toContain(
      'border-radius: var(--m3-shape-extra-small) var(--m3-shape-large) var(--m3-shape-large) var(--m3-shape-large)',
    )
    // 長訊息完整顯示，不截斷
    expect(style).not.toContain('line-clamp')
    expect(style).not.toContain('text-overflow')
  })

  it('PickupReplyMessage waiting style has an indeterminate bar that rests under reduced motion', () => {
    const wrapper = mountReply()

    expect(wrapper.get('.prm-wait__text').classes()).toContain('m3-body-large')
    expect(wrapper.get('.prm-wait .m3-icon').attributes('style')).toContain('font-size: 24px')
    expect(wrapper.find('.prm-spinner').exists()).toBe(false)
    expect(rule('.prm-progress')).toMatch(/height:\s*4px/)
    expect(rule('.prm-progress__bar')).toContain('animation: prm-indeterminate')
    expect(source).toMatch(/@keyframes prm-indeterminate/)

    const [, reduced] = rules('.prm-progress__bar')
    expect(source).toMatch(/@media \(prefers-reduced-motion: reduce\)/)
    expect(reduced).toContain('animation: none')
    expect(reduced).toContain('opacity: 0.38')
  })

  it('PickupReplyMessage compact prefers eta text and tags the source below it', () => {
    const wrapper = mountReply(
      { reply_ready_eta: '17:30', reply_message: '預計 17:30 可接送\n剩數學訂正', reply_source: 'auto' },
      true,
    )

    expect(wrapper.classes()).toContain('is-compact')
    expect(blocks(wrapper)).toEqual(['prm-compact__text', 'prm-source'])
    expect(wrapper.get('.prm-compact__text').text()).toBe('預計 17:30 可接送')
    expect(wrapper.get('.prm-compact__text').classes()).toContain('m3-title-medium')
    expect(wrapper.get('.prm-source').classes()).toContain('m3-label-small')
    expect(wrapper.get('.prm-source .m3-icon').attributes('style')).toContain('font-size: 14px')
    expect(wrapper.get('.prm-source').text()).toContain('系統自動回覆')
    expect(wrapper.text()).not.toContain('剩數學訂正')
    expect(wrapper.attributes('aria-live')).toBe('polite')
  })

  it('PickupReplyMessage compact message uses the first non empty line and truncates in one line', () => {
    const wrapper = mountReply({ reply_message: '\n  小明在洗手，馬上下來\n請由側門進入', reply_source: 'staff' }, true)

    expect(wrapper.get('.prm-compact__text').text()).toBe('小明在洗手，馬上下來')

    const style = rule('.prm-compact__text')
    expect(style).toContain('overflow: hidden')
    expect(style).toContain('text-overflow: ellipsis')
    expect(style).toContain('white-space: nowrap')
  })

  it('PickupReplyMessage compact waiting is one line with a spinner and no progress bar', () => {
    const wrapper = mountReply({}, true)

    expect(wrapper.get('.prm-wait__text').text()).toBe(WAITING_TEXT)
    expect(wrapper.get('.prm-wait__text').classes()).toContain('m3-body-medium')
    expect(wrapper.get('.prm-wait .m3-icon').attributes('style')).toContain('font-size: 18px')
    expect(wrapper.get('.prm-spinner').attributes('aria-hidden')).toBe('true')
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
    expect(wrapper.attributes('aria-live')).toBe('polite')
    expect(rule('.prm-spinner')).toMatch(/width:\s*16px/)
    expect(rule('.prm-spinner')).toMatch(/height:\s*16px/)
    expect(rule('.prm.is-compact .prm-wait__text')).toContain('text-overflow: ellipsis')
  })

  it('PickupReplyMessage replaces content in place when a ws push updates the reply', async () => {
    const wrapper = mountReply()
    const root = wrapper.element
    expect(wrapper.text()).toContain(WAITING_TEXT)

    await wrapper.setProps({
      request: request({
        reply_ready_eta: '17:40',
        reply_message: '國語訂正中，預計 17:40 可接送',
        reply_source: 'staff',
        replied_at: '2026-10-02T09:10:00Z',
      }),
    })
    expect(wrapper.element).toBe(root)
    expect(wrapper.attributes('aria-live')).toBe('polite')
    expect(wrapper.text()).not.toContain(WAITING_TEXT)
    expect(wrapper.get('.prm-eta').text()).toBe('預計 17:40 可接送')

    await wrapper.setProps({
      request: request({
        reply_ready_eta: '17:20',
        reply_message: '提早寫完了',
        reply_source: 'staff',
        replied_at: '2026-10-02T09:15:00Z',
      }),
    })
    expect(wrapper.element).toBe(root)
    expect(wrapper.get('.prm-eta').text()).toBe('預計 17:20 可接送')
    expect(wrapper.get(BUBBLE).text()).toBe('提早寫完了')
    expect(wrapper.get('.prm-time').text()).toBe('17:15 回覆')
  })

  it('PickupReplyMessage default auto reply without eta renders as an auto bubble not as waiting', () => {
    const wrapper = mountReply({ reply_message: WAITING_TEXT, reply_source: 'auto', replied_at: '2026-10-02T08:55:00Z' })

    expect(wrapper.get('.prm-source').text()).toContain('系統自動回覆')
    expect(wrapper.get(BUBBLE).text()).toBe(WAITING_TEXT)
    expect(wrapper.get('.prm-time').text()).toBe('16:55 回覆')
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
    expect(wrapper.find('.prm-wait').exists()).toBe(false)
  })

  it('PickupReplyMessage treats a blank message as no message', () => {
    const waiting = mountReply({ reply_message: '  \n ', reply_source: 'staff' })
    expect(waiting.text()).toContain(WAITING_TEXT)
    expect(waiting.find(BUBBLE).exists()).toBe(false)

    const etaOnly = mountReply({ reply_ready_eta: '17:30', reply_message: '   ', reply_source: 'staff' })
    expect(blocks(etaOnly)).toEqual(['prm-source', 'prm-eta'])
  })

  it('PickupReplyMessage is display only', () => {
    const wrapper = mountReply({ reply_ready_eta: '17:30', reply_message: '馬上下來', reply_source: 'staff' })

    expect(wrapper.text()).toContain('馬上下來')
    expect(wrapper.find('button, a, input, [tabindex], [role="button"]').exists()).toBe(false)
    expect(wrapper.attributes('tabindex')).toBeUndefined()
  })

  it('PickupReplyMessage colours come from m3 tokens only', () => {
    const style = source.slice(source.indexOf('<style'))

    expect(style).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(style).toContain('var(--m3-')
  })
})
