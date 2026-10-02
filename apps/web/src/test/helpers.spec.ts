// INFRA-013：mountWithApp 與 createApiMock
/* eslint-disable vue/one-component-per-file -- 測試用的探針元件集中在同一檔 */
import axios from 'axios'
import { defineStore } from 'pinia'
import { defineComponent, h } from 'vue'
import { useRoute } from 'vue-router'

import { createApiMock, mountWithApp } from './helpers'

const RouteProbe = defineComponent({
  name: 'RouteProbe',
  setup() {
    const route = useRoute()
    return () => h('span', String(route.params.id))
  },
})

const useCounterStore = defineStore('counter', {
  state: () => ({ count: 0 }),
  actions: {
    increment() {
      this.count += 1
    },
  },
})

const CounterProbe = defineComponent({
  name: 'CounterProbe',
  setup() {
    const counter = useCounterStore()
    return () => h('button', { onClick: () => counter.increment() }, String(counter.count))
  },
})

describe('helpers', () => {
  it('helpers mountWithApp resolves initial route', async () => {
    const { wrapper, router } = await mountWithApp(RouteProbe, {
      routes: [{ path: '/students/:id', component: RouteProbe }],
      initialRoute: '/students/42',
    })

    expect(router.currentRoute.value.path).toBe('/students/42')
    expect(wrapper.text()).toBe('42')
  })

  it('helpers mountWithApp applies pinia initial state', async () => {
    const { wrapper } = await mountWithApp(CounterProbe, {
      piniaInitialState: { counter: { count: 7 } },
    })

    expect(wrapper.text()).toBe('7')
  })

  it('helpers mountWithApp runs real actions by default', async () => {
    const { wrapper, pinia } = await mountWithApp(CounterProbe)

    await wrapper.find('button').trigger('click')

    expect(wrapper.text()).toBe('1')
    expect(useCounterStore(pinia).count).toBe(1)
  })

  it('helpers mountWithApp can stub actions', async () => {
    const { wrapper } = await mountWithApp(CounterProbe, { stubActions: true })

    await wrapper.find('button').trigger('click')

    expect(wrapper.text()).toBe('0')
  })

  it('helpers createApiMock rejects unregistered request', async () => {
    const api = axios.create()
    createApiMock(api)

    const error = await api.get('/api/unknown').then(
      () => null,
      (reason: unknown) => reason as Error,
    )

    expect(error?.message).toContain('Could not find mock')
  })

  it('helpers createApiMock replies registered route', async () => {
    const api = axios.create()
    const mock = createApiMock(api)
    mock.onGet('/api/children').reply(200, { items: [{ id: 'c1' }], total: 1 })

    const response = await api.get('/api/children')

    expect(response.status).toBe(200)
    expect(response.data).toEqual({ items: [{ id: 'c1' }], total: 1 })
  })
})
