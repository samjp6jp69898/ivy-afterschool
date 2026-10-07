import { flushPromises, type VueWrapper } from '@vue/test-utils'
import type MockAdapter from 'axios-mock-adapter'
import { ElMessage } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { adminHttp } from '@/api/http'
import type { Notification } from '@/shared/types/api'
import { useNotificationsStore } from '@/stores/notifications'
import { createApiMock, mountWithApp } from '@/test/helpers'
import NotificationBell from './NotificationBell.vue'
import notificationBellSource from './NotificationBell.vue?raw'
import NotificationPanel from './NotificationPanel.vue'

const LIST_URL = '/admin/notifications'

function makeNotification(overrides: Partial<Notification> = {}): Notification {
  return {
    id: 'n1',
    event: 'pickup.requested',
    title: '王小明 家長要來接',
    body: '家長預計 17:30 抵達。',
    payload: {},
    read_at: null,
    created_at: '2026-10-02T09:05:00Z',
    deep_link: '/pickup',
    ...overrides,
  }
}

let mock: MockAdapter

/**
 * popover 開關有延遲（showAfter 0、hideAfter 200 ms，都以 setTimeout 排程，flushPromises 等不到），
 * 以 fake timers 推進；flushPromises 走 setImmediate 不受影響。
 */
async function settle(): Promise<void> {
  await nextTick()
  await vi.advanceTimersByTimeAsync(300)
  await flushPromises()
}

async function mountBell(notifications: Record<string, unknown> = {}) {
  const mounted = await mountWithApp(NotificationBell, {
    initialRoute: '/students',
    piniaInitialState: {
      notifications: { items: [], unreadCount: 0, total: 0, loading: false, loaded: false, ...notifications },
    },
  })
  return mounted
}

function badgeContent(wrapper: VueWrapper) {
  return wrapper.find('.el-badge__content')
}

async function clickBell(wrapper: VueWrapper): Promise<void> {
  await wrapper.find('[aria-label=通知]').trigger('click')
  await settle()
}

function popover(wrapper: VueWrapper) {
  return wrapper.findComponent({ name: 'ElPopover' })
}

/** popover（role dialog）開關狀態反映在觸發按鈕的 aria-expanded */
function isOpen(wrapper: VueWrapper): boolean {
  return wrapper.find('[aria-label=通知]').attributes('aria-expanded') === 'true'
}

function panelRow(title: string): HTMLElement {
  const row = Array.from(document.body.querySelectorAll<HTMLElement>('.notif__row')).find((r) =>
    r.textContent?.includes(title),
  )
  if (!row) throw new Error(`面板中找不到通知「${title}」`)
  return row
}

describe('NotificationBell', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    mock = createApiMock(adminHttp)
  })

  afterEach(() => {
    mock.restore()
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('NotificationBell badge shows unread count', async () => {
    const { wrapper } = await mountBell({ unreadCount: 3 })
    expect(badgeContent(wrapper).text()).toBe('3')

    const store = useNotificationsStore()
    store.unreadCount = 120
    await settle()
    expect(badgeContent(wrapper).text()).toBe('99+')

    store.unreadCount = 99
    await settle()
    expect(badgeContent(wrapper).text()).toBe('99')

    store.unreadCount = 0
    await settle()
    expect(badgeContent(wrapper).exists()).toBe(false)
  })

  it('NotificationBell opens panel and loads once', async () => {
    mock.onGet(LIST_URL).reply(200, { items: [makeNotification()], total: 1, unread_count: 1 })
    const { wrapper } = await mountBell()
    expect(mock.history.get).toHaveLength(0)

    await clickBell(wrapper)

    expect(isOpen(wrapper)).toBe(true)
    expect(document.body.textContent).toContain('王小明 家長要來接')
    expect(mock.history.get).toHaveLength(1)
    expect(mock.history.get[0]?.params).toEqual({ page_size: 20 })
    expect(badgeContent(wrapper).text()).toBe('1')

    await clickBell(wrapper)
    expect(isOpen(wrapper)).toBe(false)
    await clickBell(wrapper)

    expect(isOpen(wrapper)).toBe(true)
    expect(mock.history.get).toHaveLength(1)
  })

  it('NotificationBell does not refetch when the store is already loaded', async () => {
    const { wrapper } = await mountBell({
      items: [makeNotification({ title: '陳小美 請假', deep_link: '/leaves' })],
      unreadCount: 1,
      total: 1,
      loaded: true,
    })

    await clickBell(wrapper)

    expect(document.body.textContent).toContain('陳小美 請假')
    expect(mock.history.get).toHaveLength(0)
  })

  it('NotificationBell marks read and navigates to deep link', async () => {
    mock.onGet(LIST_URL).reply(200, { items: [makeNotification()], total: 1, unread_count: 1 })
    mock.onPost('/admin/notifications/n1/read').reply(200, makeNotification({ read_at: '2026-10-02T09:06:00Z' }))
    const { wrapper, router } = await mountBell()
    await clickBell(wrapper)

    panelRow('王小明 家長要來接').click()
    await settle()

    expect(mock.history.post[0]?.url).toBe('/admin/notifications/n1/read')
    expect(router.currentRoute.value.path).toBe('/pickup')
    expect(isOpen(wrapper)).toBe(false)
    expect(useNotificationsStore().unreadCount).toBe(0)
    expect(badgeContent(wrapper).exists()).toBe(false)
  })

  it('NotificationBell still navigates when mark read fails', async () => {
    const error = vi.spyOn(ElMessage, 'error').mockReturnValue({ close: vi.fn() })
    mock.onGet(LIST_URL).reply(200, { items: [makeNotification()], total: 1, unread_count: 1 })
    mock.onPost('/admin/notifications/n1/read').reply(500, {
      error: { code: 'internal_error', message: '伺服器暫時無法處理', details: null },
    })
    const { wrapper, router } = await mountBell()
    await clickBell(wrapper)

    panelRow('王小明 家長要來接').click()
    await settle()

    expect(router.currentRoute.value.path).toBe('/pickup')
    expect(error).toHaveBeenCalledWith('伺服器暫時無法處理')
    // 標已讀失敗：store 還原未讀
    expect(useNotificationsStore().unreadCount).toBe(1)
  })

  it('NotificationBell closes without navigating when there is no deep link', async () => {
    mock.onGet(LIST_URL).reply(200, {
      items: [makeNotification({ id: 'n2', title: '林小華 取消請假', deep_link: null })],
      total: 1,
      unread_count: 1,
    })
    mock.onPost('/admin/notifications/n2/read').reply(200, makeNotification({ id: 'n2', deep_link: null }))
    const { wrapper, router } = await mountBell()
    await clickBell(wrapper)

    panelRow('林小華 取消請假').click()
    await settle()

    expect(mock.history.post[0]?.url).toBe('/admin/notifications/n2/read')
    expect(isOpen(wrapper)).toBe(false)
    expect(router.currentRoute.value.path).toBe('/students')
  })

  it('NotificationBell marks all read from the panel', async () => {
    mock.onGet(LIST_URL).reply(200, {
      items: [makeNotification(), makeNotification({ id: 'n2', title: '陳小美 請假' })],
      total: 2,
      unread_count: 2,
    })
    mock.onPost('/admin/notifications/read-all').reply(200, { updated: 2 })
    const { wrapper } = await mountBell()
    await clickBell(wrapper)
    expect(badgeContent(wrapper).text()).toBe('2')

    wrapper.findComponent(NotificationPanel).vm.$emit('read-all')
    await settle()

    expect(mock.history.post.map((c) => c.url)).toEqual(['/admin/notifications/read-all'])
    expect(badgeContent(wrapper).exists()).toBe(false)
    expect(document.body.querySelectorAll('[data-test=unread-dot]')).toHaveLength(0)
  })

  it('NotificationBell refetches with the unread filter', async () => {
    mock.onGet(LIST_URL).reply(200, { items: [makeNotification()], total: 1, unread_count: 1 })
    const { wrapper } = await mountBell()
    await clickBell(wrapper)

    wrapper.findComponent(NotificationPanel).vm.$emit('filter', true)
    await settle()

    expect(mock.history.get.map((c) => c.params)).toEqual([{ page_size: 20 }, { page_size: 20, unread_only: true }])
  })

  it('NotificationBell shows an error when loading fails', async () => {
    const error = vi.spyOn(ElMessage, 'error').mockReturnValue({ close: vi.fn() })
    mock.onGet(LIST_URL).networkError()
    const { wrapper } = await mountBell()

    await clickBell(wrapper)

    expect(error).toHaveBeenCalledWith('網路連線異常，請稍後再試')
    expect(useNotificationsStore().loaded).toBe(false)
  })

  it('NotificationBell popover follows the approved mockup', async () => {
    const { wrapper } = await mountBell()

    const props = popover(wrapper).props()
    expect(props.width).toBe(360)
    expect(props.placement).toBe('bottom-end')
    expect(props.showArrow).toBe(false)
    expect(props.trigger).toBe('click')
    // 面板可操作：dialog 讓觸發鈕帶 aria-haspopup / aria-expanded
    expect(wrapper.find('[aria-label=通知]').attributes('aria-haspopup')).toBe('dialog')
    expect(isOpen(wrapper)).toBe(false)
    // 44×44 觸控目標（happy-dom 不計算版面，以原始碼斷言）
    expect(notificationBellSource).toMatch(/\.notification-bell\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px/)
  })
})
