import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { Notification } from '@/shared/types/api'
import NotificationListItem from './NotificationListItem.vue'

const NOW = new Date('2026-10-02T10:00:00Z')

function notification(overrides: Partial<Notification> = {}): Notification {
  return {
    id: 'n1',
    event: 'homework.done',
    title: '作業已完成',
    body: '作業已完成，可以來接送了',
    payload: {},
    read_at: null,
    created_at: '2026-10-02T09:05:00Z',
    deep_link: '/homework',
    ...overrides,
  }
}

function mountItem(n: Notification) {
  return mount(NotificationListItem, { props: { notification: n, now: NOW } })
}

describe('NotificationListItem', () => {
  it('NotificationListItem unread with icon and time', () => {
    const wrapper = mountItem(notification())

    expect(wrapper.element.tagName).toBe('LI')
    expect(wrapper.classes()).toContain('is-unread')
    expect(wrapper.get('.noti-item__icon .m3-icon').text()).toBe('assignment')
    expect(wrapper.get('.noti-item__icon').attributes('aria-hidden')).toBe('true')
    expect(wrapper.get('.noti-item__title').text()).toBe('作業已完成')
    expect(wrapper.get('.noti-item__text').text()).toBe('作業已完成，可以來接送了')
    expect(wrapper.get('.noti-item__time').text()).toBe('17:05')
    expect(wrapper.get('.noti-item__dot .visually-hidden').text()).toBe('未讀')
  })

  it('NotificationListItem yesterday and older', () => {
    const yesterday = mountItem(notification({ created_at: '2026-10-01T09:05:00Z' }))
    const older = mountItem(notification({ created_at: '2026-09-20T01:00:00Z', read_at: '2026-09-20T02:00:00Z' }))

    expect(yesterday.get('.noti-item__time').text()).toBe('昨天 17:05')
    expect(older.get('.noti-item__time').text()).toBe('09/20')
    expect(older.text()).not.toContain('未讀')
    expect(older.find('.noti-item__dot').exists()).toBe(false)
    expect(older.classes()).not.toContain('is-unread')
  })

  it('NotificationListItem uses taipei day boundary', () => {
    // 台北 10/02 00:30 是 UTC 10/01 16:30：仍屬 now（台北 10/02 18:00）的同一天
    const earlyToday = mountItem(notification({ created_at: '2026-10-01T16:30:00Z' }))
    // 台北 10/01 23:59 屬昨天
    const lateYesterday = mountItem(notification({ created_at: '2026-10-01T15:59:00Z' }))

    expect(earlyToday.get('.noti-item__time').text()).toBe('00:30')
    expect(lateYesterday.get('.noti-item__time').text()).toBe('昨天 23:59')
  })

  it('NotificationListItem emits open', async () => {
    const n = notification({ id: 'n42' })
    const wrapper = mountItem(n)

    await wrapper.get('button').trigger('click')

    expect(wrapper.get('button').attributes('type')).toBe('button')
    expect(wrapper.emitted('open')).toHaveLength(1)
    expect((wrapper.emitted('open')![0]![0] as Notification).id).toBe('n42')

    const unknown = mountItem(notification({ event: 'unknown.x' as Notification['event'] }))
    expect(unknown.get('.noti-item__icon .m3-icon').text()).toBe('notifications')
  })

  it('NotificationListItem icon per event prefix', () => {
    const cases: [Notification['event'], string][] = [
      ['attendance.checked_in', 'how_to_reg'],
      ['homework.eta_updated', 'assignment'],
      ['pickup.replied', 'directions_walk'],
      ['exam.published', 'grading'],
      ['binding.completed', 'link'],
    ]
    for (const [event, icon] of cases) {
      expect(mountItem(notification({ event })).get('.noti-item__icon .m3-icon').text(), event).toBe(icon)
    }
  })
})
