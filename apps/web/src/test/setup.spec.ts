// INFRA-012：vitest 全域 setup（封鎖未 mock 的網路、隔離 storage、自動 unmount、固定時區）
import axios from 'axios'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'

const Probe = defineComponent({
  name: 'ProbeMarker',
  render: () => h('p', { class: 'probe' }, 'x'),
})

describe('harness', () => {
  it('harness blocks unmocked fetch', async () => {
    const error = await fetch('/api/x').then(
      () => null,
      (reason: unknown) => reason as Error,
    )
    expect(error?.message).toBe('Unexpected unmocked fetch in Vitest')
  })

  it('harness blocks XMLHttpRequest', () => {
    expect(() => new XMLHttpRequest()).toThrow('Unexpected unmocked XMLHttpRequest in Vitest')
  })

  it('harness makes unmocked axios fail', async () => {
    await expect(axios.get('/api/x')).rejects.toThrow(/unmocked XMLHttpRequest/)
  })

  it('harness isolates localStorage (step 1)', () => {
    localStorage.setItem('k', 'v')
    sessionStorage.setItem('s', '1')
    expect(localStorage.getItem('k')).toBe('v')
    expect(localStorage.length).toBe(1)
    expect(localStorage.key(0)).toBe('k')
  })

  it('harness isolates localStorage (step 2)', () => {
    expect(localStorage.getItem('k')).toBeNull()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.getItem('s')).toBeNull()
    expect(sessionStorage.length).toBe(0)
  })

  it('harness auto unmounts (step 1)', () => {
    const wrapper = mount(Probe, { attachTo: document.body })
    expect(document.querySelectorAll('.probe').length).toBe(1)
    expect(wrapper.text()).toBe('x')
  })

  it('harness auto unmounts (step 2)', () => {
    expect(document.querySelectorAll('.probe').length).toBe(0)
  })

  it('harness fixes timezone to Asia/Taipei', () => {
    expect(new Date('2026-09-01T16:30:00Z').getDate()).toBe(2)
    expect(new Date('2026-09-01T16:30:00Z').getHours()).toBe(0)
    expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe('Asia/Taipei')
  })
})
