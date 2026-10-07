import { createTestingPinia } from '@pinia/testing'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'
import { defineComponent, nextTick } from 'vue'
import type { StaffMe } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import { mountWithApp } from '@/test/helpers'
import { hasAnyPermission, hasPermission, usePermission } from './permission'

function makeUser(permissions: string[]): StaffMe {
  return {
    id: 'u1',
    username: 'clerk01',
    display_name: '林行政',
    role: { id: 'r1', code: 'clerk', name: '行政' },
    permissions,
    must_change_password: false,
  }
}

describe('permission utils', () => {
  it('permission utils return false without user', () => {
    createTestingPinia({
      initialState: { auth: { user: null, status: 'anonymous' } },
      stubActions: false,
      createSpy: vi.fn,
    })

    expect(useAuthStore().user).toBeNull()
    expect(hasPermission('dashboard:read')).toBe(false)
    expect(hasAnyPermission(['dashboard:read'])).toBe(false)
    expect(hasAnyPermission(['dashboard:read', 'students:read'])).toBe(false)
  })

  it('permission utils check codes', () => {
    setActivePinia(createPinia())
    useAuthStore().setUser(makeUser(['leaves:read', 'leaves:write']))

    expect(hasPermission('leaves:write')).toBe(true)
    expect(hasPermission('leaves:read')).toBe(true)
    expect(hasPermission('attendance:amend')).toBe(false)
    expect(hasAnyPermission(['attendance:amend', 'leaves:read'])).toBe(true)
    expect(hasAnyPermission(['attendance:amend', 'staff:write'])).toBe(false)
    expect(hasAnyPermission([])).toBe(false)

    // 登出後一律 false
    useAuthStore().reset()
    expect(hasPermission('leaves:write')).toBe(false)
    expect(hasAnyPermission(['leaves:write'])).toBe(false)
  })

  it('permission utils usePermission reacts to store changes', async () => {
    const Toolbar = defineComponent({
      setup() {
        const { can, canAny } = usePermission()
        return { can, canAny }
      },
      template: `
        <div>
          <button v-if="can('students:write')">新增學生</button>
          <span v-if="canAny(['students:read', 'students:write'])" class="list">學生清單</span>
        </div>
      `,
    })
    const { wrapper } = await mountWithApp(Toolbar, {
      piniaInitialState: { auth: { user: makeUser([]), status: 'authenticated' } },
    })
    const store = useAuthStore()

    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.find('.list').exists()).toBe(false)

    store.setUser(makeUser(['students:write']))
    await nextTick()

    expect(wrapper.find('button').text()).toBe('新增學生')
    expect(wrapper.find('.list').text()).toBe('學生清單')

    store.setUser(makeUser(['students:read']))
    await nextTick()

    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.find('.list').exists()).toBe(true)

    store.reset()
    await nextTick()

    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.find('.list').exists()).toBe(false)
  })
})
