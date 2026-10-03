import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { mountWithApp } from '@/test/helpers'
import AdminListToolbar from './AdminListToolbar.vue'
import type { FilterGroup } from './AdminListToolbar.vue'

const CLASS_FILTER: FilterGroup = {
  key: 'class_id',
  label: '班級',
  options: [
    { label: '低年級A班', value: 'c1' },
    { label: '高年級B班', value: 'c2' },
  ],
}

const STATUS_FILTER: FilterGroup = {
  key: 'status',
  label: '狀態',
  clearable: false,
  options: [
    { label: '生效中', value: 'active' },
    { label: '已取消', value: 'cancelled' },
  ],
}

async function mountToolbar(props: Record<string, unknown> = {}, slots: Record<string, string> = {}) {
  const { wrapper } = await mountWithApp(AdminListToolbar, { props, slots })
  return wrapper
}

/** 打開第 index 個下拉，回傳下拉選單（teleport 到 body）中可見的選項 */
async function openSelect(wrapper: VueWrapper, index: number): Promise<HTMLElement[]> {
  const select = wrapper.findAll('.el-select').at(index)
  if (!select) throw new Error(`找不到第 ${index} 個 el-select`)
  await select.find('.el-select__wrapper').trigger('click')
  await flushPromises()
  const dropdowns = Array.from(document.body.querySelectorAll<HTMLElement>('.el-select-dropdown'))
  const dropdown = dropdowns.at(-1)
  if (!dropdown) throw new Error('下拉選單沒有出現')
  return Array.from(dropdown.querySelectorAll<HTMLElement>('.el-select-dropdown__item'))
}

async function chooseOption(wrapper: VueWrapper, index: number, label: string): Promise<void> {
  const options = await openSelect(wrapper, index)
  const option = options.find((o) => o.textContent?.trim() === label)
  if (!option) throw new Error(`找不到選項「${label}」，現有：${options.map((o) => o.textContent?.trim()).join('、')}`)
  option.click()
  await flushPromises()
}

function emittedFilterValues(wrapper: VueWrapper): Record<string, unknown>[] {
  return (wrapper.emitted('update:filterValues') ?? []).map((args) => args[0] as Record<string, unknown>)
}

describe('AdminListToolbar', () => {
  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('AdminListToolbar debounces search', async () => {
    vi.useFakeTimers()
    const wrapper = await mountToolbar({ searchPlaceholder: '搜尋學生姓名' })
    const input = wrapper.find('input[aria-label="搜尋學生姓名"]')

    await input.setValue('  王小明 ')
    vi.advanceTimersByTime(299)
    expect(wrapper.emitted('update:search')).toBeUndefined()
    vi.advanceTimersByTime(1)
    expect(wrapper.emitted('update:search')?.[0]).toEqual(['王小明'])

    await input.setValue('陳')
    await input.trigger('keyup', { key: 'Enter' })
    expect(wrapper.emitted('update:search')?.[1]).toEqual(['陳'])
    // Enter 已送出，debounce 計時器被取消，不會再補送一次
    vi.advanceTimersByTime(1000)
    expect(wrapper.emitted('update:search')).toHaveLength(2)
  })

  it('AdminListToolbar clearing search emits immediately', async () => {
    vi.useFakeTimers()
    const wrapper = await mountToolbar({ search: '王小明' })
    const input = wrapper.find('.list-toolbar__search input')
    expect((input.element as HTMLInputElement).value).toBe('王小明')

    await wrapper.find('.list-toolbar__search .el-input__wrapper').trigger('mouseenter')
    await input.trigger('focus')
    await nextTick()
    await wrapper.find('.list-toolbar__search .el-input__clear').trigger('click')

    expect(wrapper.emitted('update:search')?.[0]).toEqual([''])
    vi.advanceTimersByTime(1000)
    expect(wrapper.emitted('update:search')).toHaveLength(1)
  })

  it('AdminListToolbar merges filter values', async () => {
    const filterValues = { status: 'active' }
    const wrapper = await mountToolbar({ filters: [CLASS_FILTER], filterValues })

    await chooseOption(wrapper, 0, '低年級A班')

    expect(emittedFilterValues(wrapper)[0]).toEqual({ status: 'active', class_id: 'c1' })
    expect(filterValues).toEqual({ status: 'active' })
  })

  it('AdminListToolbar clears filter to undefined and shows total', async () => {
    const wrapper = await mountToolbar({
      filters: [CLASS_FILTER],
      filterValues: { status: 'active', class_id: 'c1' },
      total: 37,
    })

    await wrapper.find('.el-select__wrapper').trigger('mouseenter')
    await nextTick()
    await wrapper.find('.el-select__clear').trigger('click')
    await flushPromises()

    const emitted = emittedFilterValues(wrapper)[0]
    expect(emitted).toHaveProperty('class_id')
    expect(emitted?.class_id).toBeUndefined()
    expect(emitted?.status).toBe('active')
    expect(wrapper.text()).toContain('共 37 筆')
  })

  it('AdminListToolbar offers an all option that clears the filter', async () => {
    const wrapper = await mountToolbar({ filters: [CLASS_FILTER], filterValues: { class_id: 'c2' } })

    const labels = (await openSelect(wrapper, 0)).map((o) => o.textContent?.trim())
    expect(labels).toEqual(['全部', '低年級A班', '高年級B班'])
    await chooseOption(wrapper, 0, '全部')

    const emitted = emittedFilterValues(wrapper)[0]
    expect(emitted).toHaveProperty('class_id')
    expect(emitted?.class_id).toBeUndefined()
  })

  it('AdminListToolbar shows all when filter value is undefined', async () => {
    const wrapper = await mountToolbar({ filters: [CLASS_FILTER], filterValues: {} })

    expect(wrapper.find('.el-select').text()).toContain('全部')
  })

  it('AdminListToolbar non-clearable filter has no all option or clear icon', async () => {
    const wrapper = await mountToolbar({ filters: [STATUS_FILTER], filterValues: { status: 'active' } })

    await wrapper.find('.el-select__wrapper').trigger('mouseenter')
    await nextTick()
    expect(wrapper.find('.el-select__clear').exists()).toBe(false)
    const labels = (await openSelect(wrapper, 0)).map((o) => o.textContent?.trim())
    expect(labels).toEqual(['生效中', '已取消'])
  })

  it('AdminListToolbar hides count when total is undefined', async () => {
    const wrapper = await mountToolbar({ filters: [CLASS_FILTER] })

    expect(wrapper.text()).not.toContain('共')
    expect(wrapper.find('[data-test=toolbar-count]').exists()).toBe(false)

    const zero = await mountToolbar({ total: 0 })
    expect(zero.find('[data-test=toolbar-count]').text()).toBe('共 0 筆')
  })

  it('AdminListToolbar renders extra-filters slot between filters and total', async () => {
    const wrapper = await mountToolbar(
      { filters: [CLASS_FILTER], total: 5 },
      { 'extra-filters': '<label class="archived">含封存</label>' },
    )

    const root = wrapper.element
    const archived = root.querySelector('.archived')
    const selects = root.querySelectorAll('.el-select')
    const lastSelect = selects[selects.length - 1]
    const count = root.querySelector('[data-test=toolbar-count]')
    expect(archived).not.toBeNull()
    expect(count?.textContent?.trim()).toBe('共 5 筆')
    expect(lastSelect!.compareDocumentPosition(archived!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(archived!.compareDocumentPosition(count!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    const without = await mountToolbar({ filters: [CLASS_FILTER], total: 5 })
    expect(without.find('[data-test=toolbar-extra-filters]').exists()).toBe(false)
  })

  it('AdminListToolbar renders actions slot only when provided', async () => {
    const wrapper = await mountToolbar({ total: 3 }, { actions: '<button>新增學生</button>' })
    expect(wrapper.find('[data-test=toolbar-actions] button').text()).toBe('新增學生')

    const without = await mountToolbar({ total: 3 })
    expect(without.find('[data-test=toolbar-actions]').exists()).toBe(false)
  })
})
