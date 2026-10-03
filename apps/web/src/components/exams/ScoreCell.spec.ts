import type { VueWrapper } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { mountWithApp } from '@/test/helpers'
import ScoreCell from './ScoreCell.vue'

type CellProps = {
  score?: number | null
  isAbsent?: boolean
  fullScore?: number
  state?: 'idle' | 'dirty' | 'saving' | 'saved' | 'error'
  errorMessage?: string
  readonly?: boolean
}

async function mountCell(props: CellProps = {}) {
  const { wrapper } = await mountWithApp(ScoreCell, {
    props: { score: null, isAbsent: false, fullScore: 100, state: 'idle', readonly: false, ...props },
  })
  return wrapper
}

async function typeAndBlur(wrapper: VueWrapper, value: string): Promise<void> {
  const input = wrapper.find('input[inputmode=decimal]')
  await input.setValue(value)
  await input.trigger('blur')
}

function changes(wrapper: VueWrapper): unknown[] {
  return (wrapper.emitted('change') ?? []).map((args) => args[0])
}

async function toggleAbsent(wrapper: VueWrapper): Promise<void> {
  const checkbox = wrapper.find('.score-cell__absent input[type=checkbox]')
  await checkbox.setValue(!(checkbox.element as HTMLInputElement).checked)
}

describe('ScoreCell', () => {
  it('ScoreCell emits valid score on blur', async () => {
    const wrapper = await mountCell()

    await typeAndBlur(wrapper, '88.5')

    expect(changes(wrapper)[0]).toEqual({ score: 88.5, is_absent: false })
    const input = wrapper.find('input[inputmode=decimal]')
    expect(input.exists()).toBe(true)
  })

  it('ScoreCell rejects out of range and extra decimals', async () => {
    const wrapper = await mountCell()

    await typeAndBlur(wrapper, '105')
    expect(wrapper.attributes('title')).toContain('需介於 0~100')
    expect(wrapper.classes()).toContain('is-invalid')
    expect(wrapper.emitted('change')).toBeUndefined()

    await typeAndBlur(wrapper, '88.25')
    expect(wrapper.attributes('title')).toContain('最多一位小數')
    expect(wrapper.emitted('change')).toBeUndefined()

    // 修正後恢復
    await typeAndBlur(wrapper, '88.2')
    expect(wrapper.classes()).not.toContain('is-invalid')
    expect(changes(wrapper)).toEqual([{ score: 88.2, is_absent: false }])
  })

  it('ScoreCell commits on Enter and rejects non-numeric', async () => {
    const wrapper = await mountCell({ fullScore: 100 })
    const input = wrapper.find('input[inputmode=decimal]')

    await input.setValue('77')
    await input.trigger('keyup', { key: 'Enter' })
    expect(changes(wrapper)).toEqual([{ score: 77, is_absent: false }])

    await typeAndBlur(wrapper, 'abc')
    expect(wrapper.attributes('title')).toContain('需介於 0~100')
    expect(changes(wrapper)).toHaveLength(1)

    await wrapper.setProps({ state: 'error', errorMessage: '儲存失敗，請重試' })
    expect(wrapper.attributes('title')).toBe('需介於 0~100')
  })

  it('ScoreCell absent toggle clears score', async () => {
    const wrapper = await mountCell({ score: 60 })

    await toggleAbsent(wrapper)
    expect(changes(wrapper)[0]).toEqual({ score: null, is_absent: true })
    await wrapper.setProps({ score: null, isAbsent: true })
    const input = wrapper.find('input[inputmode=decimal]')
    expect((input.element as HTMLInputElement).disabled).toBe(true)
    expect((input.element as HTMLInputElement).value).toBe('')

    const typed = await mountCell({ score: 60 })
    await typeAndBlur(typed, '缺')
    expect(changes(typed)[0]).toEqual({ score: null, is_absent: true })

    const dash = await mountCell()
    await typeAndBlur(dash, '-')
    expect(changes(dash)[0]).toEqual({ score: null, is_absent: true })
  })

  it('ScoreCell absent uncheck and placeholder', async () => {
    const wrapper = await mountCell({ isAbsent: true })
    const input = wrapper.find('input[inputmode=decimal]')
    expect((input.element as HTMLInputElement).disabled).toBe(true)
    expect(input.attributes('placeholder')).toBe('缺考')

    await toggleAbsent(wrapper)
    expect(changes(wrapper)[0]).toEqual({ score: null, is_absent: false })

    const already = await mountCell({ isAbsent: true })
    // disabled 的 input 仍可用程式設值觸發 blur，模擬「已缺考時再輸入缺」
    const alreadyInput = already.find('input[inputmode=decimal]')
    ;(alreadyInput.element as HTMLInputElement).disabled = false
    await typeAndBlur(already, '缺')
    expect(already.emitted('change')).toBeUndefined()
  })

  it('ScoreCell does not emit unchanged value', async () => {
    const wrapper = await mountCell({ score: 90 })

    await typeAndBlur(wrapper, '90')
    expect(wrapper.emitted('change')).toBeUndefined()

    await typeAndBlur(wrapper, '')
    expect(changes(wrapper)).toEqual([{ score: null, is_absent: false }])
  })

  it('ScoreCell state marks', async () => {
    const wrapper = await mountCell({ state: 'dirty' })
    expect(wrapper.find('[data-test=mark-dirty]').exists()).toBe(true)

    await wrapper.setProps({ state: 'saving' })
    expect(wrapper.find('[data-test=mark-saving]').exists()).toBe(true)
    expect(wrapper.find('[data-test=mark-dirty]').exists()).toBe(false)

    await wrapper.setProps({ state: 'saved' })
    expect(wrapper.find('[data-test=mark-saved]').exists()).toBe(true)

    await wrapper.setProps({ state: 'idle' })
    for (const mark of ['mark-dirty', 'mark-saving', 'mark-saved', 'mark-error']) {
      expect(wrapper.find(`[data-test=${mark}]`).exists(), mark).toBe(false)
    }
  })

  it('ScoreCell readonly display and error state', async () => {
    const readonly = await mountCell({ readonly: true, isAbsent: true })
    expect(readonly.text()).toBe('缺考')
    expect(readonly.find('input').exists()).toBe(false)

    const error = await mountCell({ score: 80, state: 'error', errorMessage: '分數超出滿分' })
    expect(error.classes()).toContain('is-error')
    expect(error.attributes('title')).toContain('分數超出滿分')
    expect(error.find('[data-test=mark-error]').exists()).toBe(true)
  })

  it('ScoreCell readonly number formatting', async () => {
    expect((await mountCell({ readonly: true, score: 90 })).text()).toBe('90')
    expect((await mountCell({ readonly: true, score: 88.5 })).text()).toBe('88.5')
    expect((await mountCell({ readonly: true, score: null })).text()).toBe('—')
  })
})
