import type { VueWrapper } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { Role } from '@/api/roles'
import { mountWithApp } from '@/test/helpers'
import RoleCardsGrid from './RoleCardsGrid.vue'

const codes = (n: number) => Array.from({ length: n }, (_, i) => `perm:${i}`)

function makeRole(overrides: Partial<Role>): Role {
  return {
    id: `r-${overrides.code ?? 'x'}`,
    code: 'x',
    name: 'x',
    description: null,
    is_system: false,
    permissions: [],
    effective_permissions: [],
    staff_count: 0,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

const SENIOR = makeRole({ code: 'senior', name: '資深老師', effective_permissions: codes(3) })
const TUTOR = makeRole({ code: 'tutor', name: '課輔老師', is_system: true, effective_permissions: codes(12), staff_count: 4 })
const ADMIN = makeRole({ code: 'admin', name: '管理員', is_system: true, effective_permissions: codes(28), staff_count: 1 })
const DIRECTOR = makeRole({ code: 'director', name: '主任', is_system: true })
const CLERK = makeRole({ code: 'clerk', name: '行政', is_system: true })
const AIDE = makeRole({ code: 'aide', name: '午班助理' })

async function mountGrid(props: Record<string, unknown>): Promise<VueWrapper> {
  const { wrapper } = await mountWithApp(RoleCardsGrid, { props: { modelValue: null, ...props } })
  return wrapper as VueWrapper
}

function cardNames(wrapper: VueWrapper): string[] {
  return wrapper.findAll('.role-card__name').map((n) => n.text())
}

function emittedIds(wrapper: VueWrapper): unknown[] {
  return (wrapper.emitted('update:modelValue') ?? []).map((args) => args[0])
}

describe('RoleCardsGrid', () => {
  it('RoleCardsGrid sorts system roles first', async () => {
    const wrapper = await mountGrid({ roles: [SENIOR, TUTOR, ADMIN] })

    expect(cardNames(wrapper)).toEqual(['管理員', '課輔老師', '資深老師'])
    const cards = wrapper.findAll('.role-card')
    expect(cards[0]!.text()).toContain('系統')
    expect(cards[1]!.text()).toContain('系統')
    expect(cards[2]!.text()).not.toContain('系統')
    expect(cards[0]!.find('.role-card__code').text()).toBe('admin')
  })

  it('RoleCardsGrid orders all system roles then custom by name', async () => {
    const wrapper = await mountGrid({ roles: [SENIOR, TUTOR, AIDE, CLERK, ADMIN, DIRECTOR] })

    expect(cardNames(wrapper).slice(0, 4)).toEqual(['管理員', '主任', '行政', '課輔老師'])
    expect([...cardNames(wrapper).slice(4)]).toEqual(['午班助理', '資深老師'].sort((a, b) => a.localeCompare(b, 'zh-Hant')))
  })

  it('RoleCardsGrid emits selection and shows counts', async () => {
    const wrapper = await mountGrid({ roles: [TUTOR, ADMIN], showStaffCount: true })

    const tutorCard = wrapper.findAll('.role-card')[1]!
    expect(tutorCard.text()).toContain('4 位員工')
    expect(tutorCard.text()).toContain('12 項')
    await tutorCard.trigger('click')

    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['r-tutor'])
  })

  it('RoleCardsGrid hides staff count by default and marks selected', async () => {
    const wrapper = await mountGrid({ roles: [TUTOR, ADMIN], modelValue: 'r-tutor' })

    const cards = wrapper.findAll('.role-card')
    expect(wrapper.text()).not.toContain('位員工')
    expect(cards[1]!.classes()).toContain('is-selected')
    expect(cards[1]!.attributes('aria-checked')).toBe('true')
    expect(cards[1]!.attributes('tabindex')).toBe('0')
    expect(cards[0]!.attributes('aria-checked')).toBe('false')
    expect(cards[0]!.attributes('tabindex')).toBe('-1')
    expect(wrapper.find('[role=radiogroup]').exists()).toBe(true)

    await cards[1]!.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('RoleCardsGrid supports keyboard selection with wrap', async () => {
    const wrapper = await mountGrid({ roles: [SENIOR, TUTOR, ADMIN], modelValue: 'r-admin' })
    const cards = () => wrapper.findAll('.role-card')

    await cards()[0]!.trigger('keydown', { key: 'ArrowDown' })
    await cards()[0]!.trigger('keydown', { key: 'ArrowUp' })
    await cards()[2]!.trigger('keydown', { key: 'Enter' })

    expect(emittedIds(wrapper)).toEqual(['r-tutor', 'r-senior', 'r-senior'])
  })

  it('RoleCardsGrid respects disabled', async () => {
    const wrapper = await mountGrid({ roles: [TUTOR, ADMIN], disabled: true })

    const card = wrapper.findAll('.role-card')[1]!
    await card.trigger('click')
    await card.trigger('keydown', { key: 'Enter' })
    await card.trigger('keydown', { key: 'ArrowDown' })

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(card.classes()).toContain('is-disabled')
    expect(card.attributes('aria-disabled')).toBe('true')
  })
})
