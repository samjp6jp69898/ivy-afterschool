import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { mountWithApp } from '@/test/helpers'
import StatCard from './StatCard.vue'

const ROUTES = [
  { path: '/', component: { render: () => null } },
  { path: '/pickup', component: { render: () => null } },
]

async function mountCard(props: Record<string, unknown>) {
  return mountWithApp(StatCard, { props, routes: ROUTES, initialRoute: '/' })
}

describe('StatCard', () => {
  it('StatCard renders label value hint and tone', async () => {
    const { wrapper } = await mountCard({ label: '未到', value: 12, tone: 'warning', hint: '含 3 位尚未點名' })

    expect(wrapper.text()).toContain('未到')
    expect(wrapper.find('[data-test=stat-value]').text()).toBe('12')
    expect(wrapper.text()).toContain('含 3 位尚未點名')
    expect(wrapper.classes()).toContain('tone-warning')

    const plain = await mountCard({ label: '在班', value: 30 })
    expect(plain.wrapper.classes()).toContain('tone-info')
  })

  it('StatCard navigates when to is set', async () => {
    const { wrapper, router } = await mountCard({ label: '待回覆接送', value: 2, to: '/pickup' })

    await wrapper.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/pickup')
  })

  it('StatCard link card shows chevron and handles Enter', async () => {
    const { wrapper, router } = await mountCard({ label: '待回覆接送', value: 2, to: '/pickup' })

    expect(wrapper.attributes('role')).toBe('link')
    expect(wrapper.attributes('tabindex')).toBe('0')
    expect(wrapper.attributes('aria-pressed')).toBeUndefined()
    expect(wrapper.find('[data-test=stat-chevron]').exists()).toBe(true)

    await wrapper.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/pickup')
    expect(wrapper.emitted('click')).toBeUndefined()
  })

  it('StatCard shows skeleton while loading', async () => {
    const { wrapper } = await mountCard({ label: '未到', value: 99, loading: true })

    expect(wrapper.text()).not.toContain('99')
    expect(wrapper.find('[data-test=stat-skeleton]').exists()).toBe(true)
    expect(wrapper.text()).toContain('未到')
  })

  it('StatCard loading hides hint and stays clickable', async () => {
    const { wrapper } = await mountCard({
      label: '未到',
      value: 12,
      hint: '含 3 位尚未點名',
      loading: true,
      clickable: true,
    })

    expect(wrapper.text()).not.toContain('含 3 位尚未點名')
    expect(wrapper.attributes('aria-busy')).toBe('true')
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
  })

  it('StatCard clickable emits click and static card does not', async () => {
    const { wrapper } = await mountCard({ label: '未交', value: 4, clickable: true, active: true })
    expect(wrapper.attributes('role')).toBe('button')
    expect(wrapper.attributes('tabindex')).toBe('0')
    expect(wrapper.attributes('aria-pressed')).toBe('true')
    expect(wrapper.classes()).toContain('is-active')
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)

    const staticCard = (await mountCard({ label: '接送', value: 2 })).wrapper
    expect(staticCard.attributes('role')).toBeUndefined()
    expect(staticCard.attributes('tabindex')).toBeUndefined()
    await staticCard.trigger('click')
    expect(staticCard.emitted('click')).toBeUndefined()
  })

  it('StatCard clickable keyboard and active style', async () => {
    const { wrapper } = await mountCard({ label: '未交', value: 4, clickable: true, active: false })
    expect(wrapper.attributes('aria-pressed')).toBe('false')
    expect(wrapper.classes()).not.toContain('is-active')

    await wrapper.trigger('keydown', { key: 'Enter' })
    await wrapper.trigger('keydown', { key: ' ' })
    expect(wrapper.emitted('click')).toHaveLength(2)

    const staticCard = (await mountCard({ label: '接送', value: 2, active: true })).wrapper
    expect(staticCard.classes()).not.toContain('is-active')
    expect(staticCard.classes()).not.toContain('is-interactive')
    expect(staticCard.find('[data-test=stat-chevron]').exists()).toBe(false)
    await staticCard.trigger('keydown', { key: 'Enter' })
    expect(staticCard.emitted('click')).toBeUndefined()
  })
})
