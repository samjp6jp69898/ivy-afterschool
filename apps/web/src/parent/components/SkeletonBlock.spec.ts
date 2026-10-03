import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SkeletonBlock from './SkeletonBlock.vue'

describe('SkeletonBlock', () => {
  it('SkeletonBlock renders count blocks', () => {
    const wrapper = mount(SkeletonBlock, { props: { variant: 'row', count: 3 } })
    expect(wrapper.findAll('.skeleton-row')).toHaveLength(3)
    expect(wrapper.attributes('aria-busy')).toBe('true')
    expect(wrapper.text()).toContain('載入中')
    expect(wrapper.attributes('role')).toBeUndefined()
    for (const row of wrapper.findAll('.skeleton-row')) {
      expect(row.attributes('aria-hidden')).toBe('true')
    }
  })

  it('SkeletonBlock applies custom size', () => {
    const line = mount(SkeletonBlock, { props: { width: '60%', height: '20px' } })
    const block = line.get('.skeleton-line')
    expect(block.attributes('style')).toContain('width: 60%')
    expect(block.attributes('style')).toContain('height: 20px')

    const card = mount(SkeletonBlock, { props: { variant: 'card', count: 2, width: '120px', height: '48px' } })
    const cards = card.findAll('.skeleton-card')
    expect(cards).toHaveLength(2)
    for (const c of cards) {
      expect(c.attributes('style')).toContain('width: 120px')
      expect(c.attributes('style')).toContain('height: 48px')
    }
  })

  it('SkeletonBlock defaults to one line', () => {
    const wrapper = mount(SkeletonBlock)
    const lines = wrapper.findAll('.skeleton-line')
    expect(lines).toHaveLength(1)
    expect(lines[0]?.attributes('style')).toBeUndefined()
    expect(wrapper.findAll('.skeleton-card')).toHaveLength(0)
    expect(wrapper.findAll('.skeleton-row')).toHaveLength(0)
  })

  it('SkeletonBlock shortens last line when width not given', () => {
    const lines = mount(SkeletonBlock, { props: { variant: 'line', count: 3 } }).findAll('.skeleton-line')
    expect(lines).toHaveLength(3)
    expect(lines[0]?.attributes('style')).toBeUndefined()
    expect(lines[1]?.attributes('style')).toBeUndefined()
    expect(lines[2]?.attributes('style')).toContain('width: 60%')

    const custom = mount(SkeletonBlock, { props: { count: 2, width: '80%' } }).findAll('.skeleton-line')
    expect(custom.map((l) => l.attributes('style'))).toEqual(['width: 80%;', 'width: 80%;'])
  })

  it('SkeletonBlock row height overrides default row height', () => {
    const rows = mount(SkeletonBlock, { props: { variant: 'row', count: 2, height: '56px' } }).findAll('.skeleton-row')
    expect(rows).toHaveLength(2)
    for (const row of rows) {
      const style = row.attributes('style') ?? ''
      expect(style).toMatch(/(^|; )height: 56px/)
      // .skeleton-row 預設的 72px 下限要一起被覆寫，否則 inline height 不生效
      expect(style).toContain('min-height: 56px')
    }
  })

  it('SkeletonBlock shortens last line when only height given', () => {
    const lines = mount(SkeletonBlock, { props: { variant: 'line', count: 3, height: '20px' } }).findAll('.skeleton-line')
    expect(lines).toHaveLength(3)
    for (const line of lines) expect(line.attributes('style')).toMatch(/(^|; )height: 20px/)
    expect(lines[0]?.attributes('style')).not.toContain('width')
    expect(lines[1]?.attributes('style')).not.toContain('width')
    expect(lines[2]?.attributes('style')).toContain('width: 60%')
  })

  it('SkeletonBlock renders card blocks', () => {
    const wrapper = mount(SkeletonBlock, { props: { variant: 'card', count: 3 } })
    expect(wrapper.findAll('.skeleton-card')).toHaveLength(3)
    expect(wrapper.findAll('.skeleton-line')).toHaveLength(0)
  })
})
