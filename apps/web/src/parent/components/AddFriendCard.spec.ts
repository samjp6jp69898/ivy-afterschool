import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AddFriendCard from './AddFriendCard.vue'
import source from './AddFriendCard.vue?raw'

const URL_OK = 'https://line.me/R/ti/p/@happy'

describe('AddFriendCard', () => {
  it('AddFriendCard renders link', () => {
    const wrapper = mount(AddFriendCard, { props: { url: URL_OK } })
    expect(wrapper.text()).toContain('加入官方帳號好友')
    expect(wrapper.text()).toContain('加入後才能收到 LINE 通知（到班、作業完成、接送回覆等）')
    const link = wrapper.findAll('a').find((a) => a.text().includes('加入好友'))
    expect(link?.attributes('href')).toBe(URL_OK)
    expect(link?.attributes('target')).toBe('_blank')
    expect(link?.attributes('rel')).toContain('noopener')
    expect(link?.attributes('rel')).toContain('noreferrer')
  })

  it.each([null, '', 'javascript:alert(1)', 'http://line.me/R/ti/p/@happy', 'HTTPS//x', ' https://line.me/x'])(
    'AddFriendCard hidden without valid url %j',
    (url) => {
      const wrapper = mount(AddFriendCard, { props: { url } })
      expect(wrapper.find('a').exists()).toBe(false)
      expect(wrapper.text()).toBe('')
      expect(wrapper.html()).not.toContain('add-friend')
    },
  )

  it('AddFriendCard keeps the title as a non-heading and the icon decorative', () => {
    const wrapper = mount(AddFriendCard, { props: { url: URL_OK } })
    expect(wrapper.find('h1,h2,h3,h4').exists()).toBe(false)
    expect(wrapper.get('.add-friend__icon').attributes('aria-hidden')).toBe('true')
    expect(wrapper.get('a').text()).toContain('person_add')
  })

  it('AddFriendCard style follows design decisions', () => {
    expect(source).toMatch(/var\(--m3-primary-container\)/)
    expect(source).toMatch(/var\(--m3-secondary-container\)/)
    expect(source).toMatch(/min-height:\s*48px/)
    expect(source).not.toMatch(/#06c755|#00b900/i)
  })
})
