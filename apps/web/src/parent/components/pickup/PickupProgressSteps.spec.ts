import { mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { ParentPickupRequest } from '../../api/pickupRequests'
import PickupProgressSteps from './PickupProgressSteps.vue'
import source from './PickupProgressSteps.vue?raw'

function request(overrides: Partial<ParentPickupRequest> = {}): ParentPickupRequest {
  return {
    id: 'r1',
    student_id: 's1',
    student_name: '王小明',
    service_date: '2026-10-02',
    status: 'pending',
    expected_arrival_at: null,
    reply_ready_eta: null,
    reply_message: null,
    reply_source: null,
    replied_at: null,
    arrived_at: null,
    completed_at: null,
    picked_up_by_name: null,
    cancelled_at: null,
    created_at: '2026-10-02T09:00:00Z',
    can_cancel: true,
    can_mark_arrived: true,
    ...overrides,
  }
}

function mountSteps(overrides: Partial<ParentPickupRequest> = {}) {
  return mount(PickupProgressSteps, { props: { request: request(overrides) } })
}

function step(wrapper: VueWrapper, index: number): DOMWrapper<Element> {
  const found = wrapper.findAll('.pps-step')[index]
  if (!found) throw new Error(`第 ${index + 1} 步不存在`)
  return found
}

/** 四步各自是否完成（is-done） */
function doneFlags(wrapper: VueWrapper): boolean[] {
  return wrapper.findAll('.pps-step').map((li) => li.classes().includes('is-done'))
}

/** 目前步驟的索引（aria-current="step"）；沒有回 -1 */
function currentIndex(wrapper: VueWrapper): number {
  return wrapper.findAll('.pps-step').findIndex((li) => li.attributes('aria-current') === 'step')
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

describe('PickupProgressSteps', () => {
  it('PickupProgressSteps pending current step', () => {
    const wrapper = mountSteps({ status: 'pending', created_at: '2026-10-02T09:00:00Z' })

    expect(step(wrapper, 0).text()).toContain('17:00')
    expect(step(wrapper, 0).classes()).toContain('is-done')
    expect(step(wrapper, 1).attributes('aria-current')).toBe('step')
    expect(step(wrapper, 1).text()).toContain('老師已確認')
    expect(doneFlags(wrapper)).toEqual([true, false, false, false])
    expect(currentIndex(wrapper)).toBe(1)
  })

  it('PickupProgressSteps arrived skips ack', () => {
    const wrapper = mountSteps({ status: 'arrived', arrived_at: '2026-10-02T09:32:00Z' })

    expect(step(wrapper, 1).classes()).toContain('is-done')
    expect(step(wrapper, 2).classes()).toContain('is-done')
    expect(step(wrapper, 3).attributes('aria-current')).toBe('step')
    expect(doneFlags(wrapper)).toEqual([true, true, true, false])
    expect(currentIndex(wrapper)).toBe(3)
  })

  it('PickupProgressSteps completed shows picker', () => {
    const wrapper = mountSteps({
      status: 'completed',
      arrived_at: '2026-10-02T09:32:00Z',
      completed_at: '2026-10-02T09:40:00Z',
      picked_up_by_name: '王媽媽',
    })

    expect(step(wrapper, 3).text()).toContain('17:40')
    expect(step(wrapper, 3).text()).toContain('王媽媽')
    expect(step(wrapper, 3).get('.pps-sub').text()).toBe('由 王媽媽 接走')
    expect(wrapper.find('[aria-current]').exists()).toBe(false)
    expect(doneFlags(wrapper)).toEqual([true, true, true, true])
  })

  it('PickupProgressSteps cancelled and expired', () => {
    const cancelled = mountSteps({ status: 'cancelled', cancelled_at: '2026-10-02T09:10:00Z' })
    expect(cancelled.text()).toContain('已取消（17:10）')
    expect(cancelled.find('[aria-current]').exists()).toBe(false)

    const expired = mountSteps({ status: 'expired' })
    expect(expired.text()).toContain('接送請求已逾時自動結束')
    expect(expired.text()).not.toContain('已取消')
    expect(expired.find('[aria-current]').exists()).toBe(false)
  })

  it('PickupProgressSteps current step hint', () => {
    const pending = mountSteps({ status: 'pending' })
    expect(step(pending, 1).text()).toContain('等待老師確認')

    const acknowledged = mountSteps({ status: 'acknowledged' })
    expect(step(acknowledged, 2).text()).toContain('抵達安親班門口後請按「我到了」')
    expect(acknowledged.text()).not.toContain('等待老師確認')

    const arrived = mountSteps({ status: 'arrived', arrived_at: '2026-10-02T09:32:00Z' })
    expect(step(arrived, 3).text()).toContain('老師正在帶小孩出來')
    expect(arrived.text()).not.toContain('抵達安親班門口後請按「我到了」')
  })

  it('PickupProgressSteps completed without arrival', () => {
    const wrapper = mountSteps({
      status: 'completed',
      arrived_at: null,
      completed_at: '2026-10-02T09:40:00Z',
      picked_up_by_name: '陳叔叔',
    })

    expect(step(wrapper, 2).classes()).toContain('is-done')
    expect(step(wrapper, 2).text()).not.toMatch(/\d\d:\d\d/)
    expect(step(wrapper, 3).text()).toContain('17:40')
    expect(doneFlags(wrapper)).toEqual([true, true, true, true])
  })

  it('PickupProgressSteps lists four steps in order inside a labelled ol', () => {
    const wrapper = mountSteps()
    const list = wrapper.get('ol')

    expect(list.attributes('aria-label')).toBe('接送進度')
    expect(wrapper.findAll('ol')).toHaveLength(1)
    const labels = wrapper.findAll('.pps-step .pps-label').map((el) => el.element.firstChild?.textContent)
    expect(labels).toEqual(['已送出', '老師已確認', '我已到達', '已接走'])
    expect(list.findAll('li')).toHaveLength(4)
  })

  it('PickupProgressSteps announces step state in text not only by colour', () => {
    const wrapper = mountSteps({ status: 'acknowledged' })

    expect(step(wrapper, 0).get('.pps-label .visually-hidden').text()).toBe('（已完成）')
    expect(step(wrapper, 1).get('.pps-label .visually-hidden').text()).toBe('（已完成）')
    expect(step(wrapper, 2).get('.pps-label .visually-hidden').text()).toBe('（進行中）')
    expect(step(wrapper, 3).get('.pps-label .visually-hidden').text()).toBe('（未完成）')
    expect(rule('.visually-hidden')).toContain('position: absolute')
  })

  it('PickupProgressSteps markers show a check for done steps only', () => {
    const wrapper = mountSteps({ status: 'acknowledged' })

    for (const index of [0, 1]) {
      const marker = step(wrapper, index).get('.pps-marker')
      expect(marker.get('.m3-icon').text()).toBe('check')
      expect(marker.attributes('aria-hidden')).toBe('true')
    }
    for (const index of [2, 3]) {
      const marker = step(wrapper, index).get('.pps-marker')
      expect(marker.find('.m3-icon').exists()).toBe(false)
      expect(marker.attributes('aria-hidden')).toBe('true')
    }
    expect(step(wrapper, 2).classes()).toContain('is-current')
    expect(step(wrapper, 3).classes()).not.toContain('is-current')
  })

  it('PickupProgressSteps connector turns primary when the next step is done', () => {
    const lines = (wrapper: VueWrapper) =>
      wrapper.findAll('.pps-step').map((li) => li.classes().includes('is-line-done'))

    expect(lines(mountSteps({ status: 'pending' }))).toEqual([false, false, false, false])
    expect(lines(mountSteps({ status: 'acknowledged' }))).toEqual([true, false, false, false])
    expect(lines(mountSteps({ status: 'arrived', arrived_at: '2026-10-02T09:32:00Z' }))).toEqual([
      true,
      true,
      false,
      false,
    ])
    expect(
      lines(mountSteps({ status: 'completed', arrived_at: '2026-10-02T09:32:00Z', completed_at: '2026-10-02T09:40:00Z' })),
    ).toEqual([true, true, true, false])

    expect(rule('.pps-step::before')).toMatch(/width:\s*2px/)
    expect(rule('.pps-step::before')).toContain('background: var(--m3-outline-variant)')
    expect(rule('.pps-step.is-line-done::before')).toContain('background: var(--m3-primary)')
    expect(rule('.pps-step:last-child::before')).toContain('display: none')
  })

  it('PickupProgressSteps shows times only where the api has them', () => {
    const wrapper = mountSteps({
      status: 'arrived',
      created_at: '2026-10-02T09:00:00Z',
      arrived_at: '2026-10-02T09:32:00Z',
    })

    expect(step(wrapper, 0).get('.pps-time').text()).toBe('17:00')
    // API 沒有 acknowledged_at：老師已確認一律不顯示時間
    expect(step(wrapper, 1).find('.pps-time').exists()).toBe(false)
    expect(step(wrapper, 2).get('.pps-time').text()).toBe('17:32')
    // 還沒接走：已接走沒有時間
    expect(step(wrapper, 3).find('.pps-time').exists()).toBe(false)

    const acknowledged = mountSteps({ status: 'acknowledged' })
    expect(step(acknowledged, 1).find('.pps-time').exists()).toBe(false)
    expect(step(acknowledged, 2).find('.pps-time').exists()).toBe(false)
  })

  it('PickupProgressSteps converts times to taipei clock', () => {
    // UTC 10/01 16:30 = 台北 10/02 00:30
    const wrapper = mountSteps({ created_at: '2026-10-01T16:30:00Z' })

    expect(step(wrapper, 0).get('.pps-time').text()).toBe('00:30')
  })

  it('PickupProgressSteps arrive now shortcut creates and arrives at the same time', () => {
    const wrapper = mountSteps({
      status: 'arrived',
      created_at: '2026-10-02T09:25:00Z',
      arrived_at: '2026-10-02T09:25:00Z',
    })

    expect(doneFlags(wrapper)).toEqual([true, true, true, false])
    expect(step(wrapper, 0).get('.pps-time').text()).toBe('17:25')
    expect(step(wrapper, 2).get('.pps-time').text()).toBe('17:25')
    expect(step(wrapper, 3).get('.pps-sub').text()).toBe('老師正在帶小孩出來')
  })

  it('PickupProgressSteps shows the hint only on the current step', () => {
    for (const [status, hint] of [
      ['pending', '等待老師確認'],
      ['acknowledged', '抵達安親班門口後請按「我到了」'],
      ['arrived', '老師正在帶小孩出來'],
    ] as const) {
      const wrapper = mountSteps({ status, arrived_at: status === 'arrived' ? '2026-10-02T09:32:00Z' : null })
      const subs = wrapper.findAll('.pps-sub')
      expect(subs.map((el) => el.text()), status).toEqual([hint])
      expect(subs[0]?.element.closest('.pps-step')?.getAttribute('aria-current'), status).toBe('step')
    }

    for (const status of ['completed', 'cancelled', 'expired'] as const) {
      const wrapper = mountSteps({ status })
      expect(wrapper.text(), status).not.toContain('等待老師確認')
      expect(wrapper.text(), status).not.toContain('抵達安親班門口後請按')
      expect(wrapper.text(), status).not.toContain('老師正在帶小孩出來')
    }
  })

  it('PickupProgressSteps completed without picker name shows no picker line', () => {
    const wrapper = mountSteps({
      status: 'completed',
      arrived_at: '2026-10-02T09:32:00Z',
      completed_at: '2026-10-02T09:40:00Z',
      picked_up_by_name: null,
    })

    expect(step(wrapper, 3).find('.pps-sub').exists()).toBe(false)
    expect(step(wrapper, 3).get('.pps-time').text()).toBe('17:40')
    expect(wrapper.text()).not.toContain('由')
  })

  it('PickupProgressSteps cancelled fades unfinished steps and explains below', () => {
    const wrapper = mountSteps({ status: 'cancelled', cancelled_at: '2026-10-02T09:10:00Z' })

    expect(wrapper.classes()).toContain('is-ended')
    expect(doneFlags(wrapper)).toEqual([true, false, false, false])
    const ended = wrapper.get('.pps-ended')
    expect(ended.get('.pps-ended__text').text()).toBe('已取消（17:10）')
    expect(ended.get('.m3-icon').text()).toBe('cancel')
    expect(wrapper.element.lastElementChild).toBe(ended.element)

    // 到達後才取消：已完成的步驟保持
    const afterArrival = mountSteps({
      status: 'cancelled',
      arrived_at: '2026-10-02T09:32:00Z',
      cancelled_at: '2026-10-02T09:36:00Z',
    })
    expect(doneFlags(afterArrival)).toEqual([true, true, true, false])
    expect(afterArrival.get('.pps-ended__text').text()).toBe('已取消（17:36）')

    // 沒有 cancelled_at 時不顯示「（—）」
    const noTime = mountSteps({ status: 'cancelled', cancelled_at: null })
    expect(noTime.get('.pps-ended__text').text()).toBe('已取消')
  })

  it('PickupProgressSteps expired explains below with its own icon', () => {
    const wrapper = mountSteps({ status: 'expired' })

    expect(wrapper.classes()).toContain('is-ended')
    const ended = wrapper.get('.pps-ended')
    expect(ended.get('.pps-ended__text').text()).toBe('接送請求已逾時自動結束')
    expect(ended.get('.m3-icon').text()).toBe('timer_off')
    expect(doneFlags(wrapper)).toEqual([true, false, false, false])
  })

  it('PickupProgressSteps open and completed requests have no ended block', () => {
    for (const status of ['pending', 'acknowledged', 'arrived', 'completed'] as const) {
      const wrapper = mountSteps({ status, arrived_at: '2026-10-02T09:32:00Z' })
      expect(wrapper.findAll('.pps-step'), status).toHaveLength(4)
      expect(wrapper.find('.pps-ended').exists(), status).toBe(false)
      expect(wrapper.classes(), status).not.toContain('is-ended')
    }
  })

  it('PickupProgressSteps is display only', () => {
    const wrapper = mountSteps({ status: 'acknowledged' })

    expect(wrapper.text()).toContain('老師已確認')
    expect(wrapper.find('button, a, input, [tabindex], [role="button"]').exists()).toBe(false)
  })

  it('PickupProgressSteps updates in place when a ws push advances the request', async () => {
    const wrapper = mountSteps({ status: 'pending' })
    expect(currentIndex(wrapper)).toBe(1)

    await wrapper.setProps({ request: request({ status: 'acknowledged' }) })
    expect(currentIndex(wrapper)).toBe(2)
    expect(doneFlags(wrapper)).toEqual([true, true, false, false])

    await wrapper.setProps({
      request: request({ status: 'completed', arrived_at: '2026-10-02T09:32:00Z', completed_at: '2026-10-02T09:40:00Z', picked_up_by_name: '王媽媽' }),
    })
    expect(currentIndex(wrapper)).toBe(-1)
    expect(doneFlags(wrapper)).toEqual([true, true, true, true])
    expect(step(wrapper, 3).text()).toContain('王媽媽')
  })

  it('PickupProgressSteps style follows design decisions', () => {
    expect(rule('.pps-step')).toContain('grid-template-columns: 24px minmax(0, 1fr) auto')
    expect(rule('.pps-marker')).toMatch(/width:\s*24px/)
    expect(rule('.pps-marker')).toMatch(/height:\s*24px/)
    expect(rule('.pps-marker')).toContain('border: 2px solid var(--m3-outline-variant)')
    expect(rule('.pps-step.is-done .pps-marker')).toContain('background: var(--m3-primary)')
    expect(rule('.pps-step.is-current .pps-marker')).toContain('border-color: var(--m3-primary)')
    const dot = rule('.pps-step.is-current .pps-marker::after')
    expect(dot).toMatch(/width:\s*10px/)
    expect(dot).toMatch(/height:\s*10px/)
    expect(dot).toContain('background: var(--m3-primary)')
    expect(rule('.pps-step.is-current .pps-label')).toMatch(/font-weight:\s*500/)
    expect(rule('.pps-step.is-current .pps-sub')).toContain('color: var(--m3-primary)')
    expect(rule('.pps-time')).toContain('font-variant-numeric: tabular-nums')

    // 終態：未完成步驟（標記與文字）降為 38%，已完成保持原色
    expect(source).toMatch(/\.pps\.is-ended \.pps-step:not\(\.is-done\) \.pps-marker,\s*\.pps\.is-ended \.pps-step:not\(\.is-done\) \.pps-body \{[^}]*opacity:\s*0\.38/)

    // 結束列：surface-container-high 圓角 12px，不用 error 色
    const ended = rule('.pps-ended')
    expect(ended).toContain('background: var(--m3-surface-container-high)')
    expect(ended).toContain('border-radius: var(--m3-shape-medium)')
    expect(source).not.toContain('--m3-error')
  })

  it('PickupProgressSteps colours come from m3 tokens only', () => {
    const style = source.slice(source.indexOf('<style'))

    expect(style).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(style).toContain('var(--m3-')
  })
})
