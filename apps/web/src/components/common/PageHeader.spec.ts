import { describe, expect, it } from 'vitest'
import { mountWithApp } from '@/test/helpers'
import PageHeader from './PageHeader.vue'

describe('PageHeader', () => {
  it('PageHeader renders title subtitle and actions', async () => {
    const { wrapper } = await mountWithApp(PageHeader, {
      props: { title: '學生工作台', subtitle: '共 120 位在學' },
      slots: { actions: '<button>新增學生</button>' },
    })

    expect(wrapper.find('h1').text()).toBe('學生工作台')
    expect(wrapper.find('[data-test=page-subtitle]').text()).toBe('共 120 位在學')
    expect(wrapper.find('[data-test=page-actions] button').text()).toBe('新增學生')
  })

  it('PageHeader omits subtitle element when absent', async () => {
    const { wrapper } = await mountWithApp(PageHeader, { props: { title: '考試' } })

    expect(wrapper.find('h1').text()).toBe('考試')
    expect(wrapper.find('[data-test=page-subtitle]').exists()).toBe(false)
  })

  it('PageHeader omits actions container when slot is empty', async () => {
    const { wrapper } = await mountWithApp(PageHeader, { props: { title: '考試' } })

    expect(wrapper.find('[data-test=page-actions]').exists()).toBe(false)
  })

  it('PageHeader renders default slot below the title', async () => {
    const { wrapper } = await mountWithApp(PageHeader, {
      props: { title: '請假' },
      slots: { default: '<span class="summary">篩選：病假</span>' },
    })

    const extra = wrapper.find('[data-test=page-extra]')
    expect(extra.find('.summary').text()).toBe('篩選：病假')
    // 附加內容在標題區塊內（h1 之後），不與 actions 並列
    const body = wrapper.find('h1').element.parentElement
    expect(body?.contains(extra.element)).toBe(true)
  })
})
