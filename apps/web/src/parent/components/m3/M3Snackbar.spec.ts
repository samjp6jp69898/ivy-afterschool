import { DOMWrapper, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import type { SnackbarItem } from '../../stores/snackbar'
import M3Snackbar from './M3Snackbar.vue'

// @vue/test-utils 預設把 <Transition> 換成 stub：進出場立即完成，不等 happy-dom 不會觸發的 transitionend
function item(overrides: Partial<SnackbarItem> = {}): SnackbarItem {
  return { id: 1, message: '已送出接送通知', tone: 'info', duration: 4000, ...overrides }
}

describe('M3Snackbar', () => {
  it('M3Snackbar renders message with live region', () => {
    const wrapper = mount(M3Snackbar, { props: { item: item() } })
    const bar = wrapper.get('.m3-snackbar')
    expect(bar.text()).toContain('已送出接送通知')
    expect(bar.attributes('role')).toBe('status')
    expect(bar.attributes('aria-live')).toBe('polite')
    expect(bar.find('button').exists()).toBe(false)
  })

  it('M3Snackbar action runs and dismisses', async () => {
    const onClick = vi.fn()
    const wrapper = mount(M3Snackbar, {
      props: { item: item({ message: '資料更新失敗', action: { label: '重試', onClick } }) },
    })
    const button = wrapper.get('button.m3-snackbar__action')
    expect(button.text()).toBe('重試')
    expect(button.attributes('type')).toBe('button')
    await button.trigger('click')
    expect(onClick).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('dismiss')).toEqual([[1]])
  })

  it('M3Snackbar error tone uses alert and icon', () => {
    const error = mount(M3Snackbar, { props: { item: item({ id: 7, message: '儲存失敗，請稍後再試', tone: 'error' }) } })
    const bar = error.get('.m3-snackbar')
    expect(bar.attributes('role')).toBe('alert')
    expect(bar.attributes('aria-live')).toBeUndefined()
    expect(bar.classes()).toContain('m3-snackbar--error')
    const icon = bar.get('.m3-snackbar__icon')
    expect(icon.text()).toBe('error')
    expect(icon.attributes('style')).toMatch(/FILL["'] 1/)
    expect(icon.attributes('aria-hidden')).toBe('true')

    const info = mount(M3Snackbar, { props: { item: item() } })
    expect(info.find('.m3-snackbar__icon').exists()).toBe(false)

    const empty = mount(M3Snackbar, { props: { item: null } })
    expect(empty.find('.m3-snackbar').exists()).toBe(false)
    expect(empty.text()).toBe('')
  })

  it('M3Snackbar swaps to next item', async () => {
    const wrapper = mount(M3Snackbar, { props: { item: item() } })
    const first = wrapper.get('.m3-snackbar').element
    await wrapper.setProps({ item: item({ id: 2, message: '已取消接送' }) })
    expect(wrapper.findAll('.m3-snackbar')).toHaveLength(1)
    // 以 item.id 為 key：換則是新元素（走出場 / 進場），不是原地改字
    expect(wrapper.get('.m3-snackbar').element).not.toBe(first)
    expect(wrapper.get('.m3-snackbar').text()).toBe('已取消接送')
    await wrapper.setProps({ item: null })
    expect(wrapper.find('.m3-snackbar').exists()).toBe(false)
  })

  it('M3Snackbar leaving item action does not run next item', async () => {
    const retryA = vi.fn()
    const undoB = vi.fn()
    // 用真的 Transition：舊的一則在 leave 期間仍留在畫面上
    const wrapper = mount(M3Snackbar, {
      props: { item: item({ id: 1, message: '資料更新失敗', action: { label: '重試A', onClick: retryA } }) },
      global: { stubs: { transition: false } },
      attachTo: document.body,
    })
    await wrapper.setProps({ item: item({ id: 2, message: '已刪除', action: { label: '復原B', onClick: undoB } }) })
    await nextTick()
    // out-in：B 要等 A 出場完才進場，此時元件根節點是佔位 comment，A 仍掛在 DOM 上
    const bars = document.body.querySelectorAll('.m3-snackbar')
    expect(bars).toHaveLength(1)
    const leaving = new DOMWrapper(bars[0] as HTMLElement)
    expect(leaving.text()).toContain('重試A')
    expect(leaving.classes()).toContain('m3-snackbar-leave-active')
    await leaving.get('button').trigger('click')
    expect(retryA).not.toHaveBeenCalled()
    expect(undoB).not.toHaveBeenCalled()
    expect(wrapper.emitted('dismiss')).toBeUndefined()
  })
})
