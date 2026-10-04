import { describe, expect, it } from 'vitest'
import { NOTIFICATION_EVENT_LABELS } from '@/shared/constants/statusLabels'
import type { Notification } from '@/shared/types/api'
import { mountWithApp } from '@/test/helpers'
import NotificationPanel from './NotificationPanel.vue'

function makeNotification(overrides: Partial<Notification> = {}): Notification {
  return {
    id: 'n1',
    event: 'pickup.requested',
    title: '王小明家長發起接送',
    body: '預計 17:30 抵達',
    payload: {},
    read_at: null,
    created_at: '2026-10-02T08:05:00Z',
    deep_link: '/pickup',
    ...overrides,
  }
}

async function mountPanel(props: Record<string, unknown> = {}) {
  const { wrapper } = await mountWithApp(NotificationPanel, {
    props: { items: [makeNotification()], unreadCount: 1, loading: false, ...props },
  })
  return wrapper
}

function buttonByText(wrapper: Awaited<ReturnType<typeof mountPanel>>, text: string) {
  const button = wrapper.findAll('button').find((b) => b.text() === text)
  if (!button) throw new Error(`找不到按鈕「${text}」`)
  return button
}

describe('NotificationPanel', () => {
  it('NotificationPanel renders rows with event label and time', async () => {
    const wrapper = await mountPanel()
    const text = wrapper.text()
    expect(text).toContain('王小明家長發起接送')
    expect(text).toContain('預計 17:30 抵達')
    expect(text).toContain('2026/10/02 16:05')
    expect(text).toContain(NOTIFICATION_EVENT_LABELS['pickup.requested'])
    expect(wrapper.find('[data-test=unread-dot]').exists()).toBe(true)
  })

  it('NotificationPanel hides unread dot for read rows', async () => {
    const wrapper = await mountPanel({
      items: [makeNotification({ read_at: '2026-10-02T08:10:00Z' })],
      unreadCount: 0,
    })
    expect(wrapper.find('[data-test=unread-dot]').exists()).toBe(false)
    expect(wrapper.find('.notif__row').classes()).not.toContain('is-unread')
  })

  it('NotificationPanel emits open and read-all', async () => {
    const wrapper = await mountPanel()
    await wrapper.find('.notif__row').trigger('click')
    const opened = wrapper.emitted('open')
    expect((opened?.[0]?.[0] as Notification).id).toBe('n1')

    await buttonByText(wrapper, '全部標為已讀').trigger('click')
    expect(wrapper.emitted('read-all')).toHaveLength(1)

    await wrapper.setProps({ unreadCount: 0 })
    expect(buttonByText(wrapper, '全部標為已讀').attributes('disabled')).toBeDefined()
  })

  it('NotificationPanel shows skeleton instead of rows while loading', async () => {
    const wrapper = await mountPanel({ loading: true })
    expect(wrapper.findAll('.notif__skeleton')).toHaveLength(3)
    expect(wrapper.find('.notif__row').exists()).toBe(false)
  })

  it('NotificationPanel empty states and filter', async () => {
    const wrapper = await mountPanel({ items: [], unreadCount: 0 })
    expect(wrapper.text()).toContain('目前沒有通知')

    const unreadOption = wrapper
      .findAll('.el-segmented__item')
      .find((el) => el.text() === '未讀')
    if (!unreadOption) throw new Error('找不到「未讀」選項')
    await unreadOption.find('input').setValue(true)
    expect(wrapper.emitted('filter')?.[0]).toEqual([true])
    expect(wrapper.text()).toContain('沒有未讀通知')
  })
})
