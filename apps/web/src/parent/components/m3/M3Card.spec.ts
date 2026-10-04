import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { defineComponent } from 'vue'
import M3Card from './M3Card.vue'
import M3Switch from './M3Switch.vue'

describe('M3Card', () => {
  it('M3Card renders slots', () => {
    const wrapper = mount(M3Card, {
      slots: {
        header: '<h3>今日狀態</h3>',
        default: '<p>已到班</p>',
        actions: '<button>接送</button>',
      },
    })
    const text = wrapper.text()
    expect(text.indexOf('今日狀態')).toBeGreaterThanOrEqual(0)
    expect(text.indexOf('已到班')).toBeGreaterThan(text.indexOf('今日狀態'))
    expect(text.indexOf('接送')).toBeGreaterThan(text.indexOf('已到班'))
    expect(wrapper.get('.m3-card__header').text()).toBe('今日狀態')
    expect(wrapper.get('.m3-card__actions').text()).toBe('接送')
  })

  it('M3Card omits empty header and actions wrappers', () => {
    const wrapper = mount(M3Card, { slots: { default: '<p>已到班</p>' } })
    expect(wrapper.find('.m3-card__header').exists()).toBe(false)
    expect(wrapper.find('.m3-card__actions').exists()).toBe(false)
  })

  it('M3Card clickable keyboard activation', async () => {
    const wrapper = mount(M3Card, { props: { clickable: true } })
    expect(wrapper.attributes('role')).toBe('button')
    expect(wrapper.attributes('tabindex')).toBe('0')
    await wrapper.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('click')).toHaveLength(1)
    const keyPayload = wrapper.emitted('click')?.[0]?.[0]
    expect(keyPayload).toBeInstanceOf(KeyboardEvent)
    expect((keyPayload as KeyboardEvent).key).toBe('Enter')
    await wrapper.trigger('keydown', { key: ' ' })
    expect(wrapper.emitted('click')).toHaveLength(2)
    await wrapper.trigger('keydown', { key: 'a' })
    expect(wrapper.emitted('click')).toHaveLength(2)
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(3)
    expect(wrapper.emitted('click')?.[2]?.[0]).toBeInstanceOf(MouseEvent)
  })

  it('M3Card space key prevents page scroll', () => {
    const wrapper = mount(M3Card, { props: { clickable: true } })
    const event = new KeyboardEvent('keydown', { key: ' ', cancelable: true, bubbles: true })
    wrapper.element.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
  })

  it('M3Card ignores keys from nested controls', () => {
    const wrapper = mount(M3Card, {
      props: { clickable: true },
      slots: { default: '<input class="inner" />' },
    })
    const event = new KeyboardEvent('keydown', { key: ' ', cancelable: true, bubbles: true })
    wrapper.get('.inner').element.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
    expect(wrapper.emitted('click')).toBeUndefined()
  })

  it('M3Card ignores key repeat', () => {
    const wrapper = mount(M3Card, { props: { clickable: true } })
    const first = new KeyboardEvent('keydown', { key: ' ', cancelable: true, bubbles: true })
    wrapper.element.dispatchEvent(first)
    const repeats = Array.from({ length: 8 }, () => {
      const event = new KeyboardEvent('keydown', { key: ' ', repeat: true, cancelable: true, bubbles: true })
      wrapper.element.dispatchEvent(event)
      return event
    })
    expect(wrapper.emitted('click')).toHaveLength(1)
    // repeat 仍要擋掉 Space 的預設捲動
    expect(repeats.every((e) => e.defaultPrevented)).toBe(true)
    wrapper.element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', repeat: true, bubbles: true }))
    expect(wrapper.emitted('click')).toHaveLength(1)
  })

  it('M3Card ignores clicks from nested controls', async () => {
    const wrapper = mount(
      defineComponent({
        components: { M3Card, M3Switch },
        data: () => ({ on: false, clicks: 0 }),
        template: `<M3Card clickable @click="clicks++">
          <p class="body">病假 10/06</p>
          <input class="field" />
          <a class="link" href="#detail">詳情</a>
          <M3Switch v-model="on" label="到班通知" />
          <template #actions><button type="button" class="pickup">我要來接</button></template>
        </M3Card>`,
      }),
    )
    const card = wrapper.findComponent(M3Card)
    await wrapper.get('.pickup').trigger('click')
    await wrapper.get('.field').trigger('click')
    await wrapper.get('.link').trigger('click')
    await wrapper.get('[role="switch"]').trigger('click')
    expect(card.emitted('click')).toBeUndefined()
    // Switch 自己照常切換
    expect(wrapper.get('[role="switch"]').attributes('aria-checked')).toBe('true')

    for (const key of ['Enter', ' ']) {
      const event = new KeyboardEvent('keydown', { key, cancelable: true, bubbles: true })
      wrapper.get('.pickup').element.dispatchEvent(event)
      expect(event.defaultPrevented).toBe(false)
    }
    expect(card.emitted('click')).toBeUndefined()

    // 點卡片本身的文字仍會觸發
    await wrapper.get('.body').trigger('click')
    expect(card.emitted('click')).toHaveLength(1)
  })

  it('M3Card non clickable ignores click', async () => {
    const wrapper = mount(M3Card)
    await wrapper.trigger('click')
    await wrapper.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('click')).toBeUndefined()
    expect(wrapper.attributes('role')).toBeUndefined()
    expect(wrapper.attributes('tabindex')).toBeUndefined()
  })

  it('M3Card variant class', () => {
    const filled = mount(M3Card)
    expect(filled.classes()).toContain('m3-card--filled')
    const elevated = mount(M3Card, { props: { variant: 'elevated' } })
    expect(elevated.classes()).toContain('m3-card--elevated')
    expect(elevated.classes()).not.toContain('m3-card--filled')
    expect(mount(M3Card, { props: { variant: 'outlined' } }).classes()).toContain('m3-card--outlined')
  })

  it('M3Card padding class', () => {
    expect(mount(M3Card).classes()).toContain('m3-card--pad-md')
    expect(mount(M3Card, { props: { padding: 'sm' } }).classes()).toContain('m3-card--pad-sm')
    const none = mount(M3Card, { props: { padding: 'none' } })
    expect(none.classes()).toContain('m3-card--pad-none')
    expect(none.classes()).not.toContain('m3-card--pad-md')
  })
})
