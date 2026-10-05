import { flushPromises, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import type { JsonSchema } from '@/api/settings'
import { mountWithApp } from '@/test/helpers'
import JsonSchemaForm from './JsonSchemaForm.vue'

// Pydantic 風格的 schema fixture（BACKEND-106 registry）
const TIME = '^([01]\\d|2[0-3]):[0-5]\\d$'

const SERVICE_HOURS: JsonSchema = {
  title: '營業時段',
  type: 'object',
  additionalProperties: false,
  required: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat'],
  $defs: {
    DayHours: {
      title: '單日營業時段',
      type: 'object',
      additionalProperties: false,
      required: ['open', 'start', 'end'],
      properties: {
        open: { type: 'boolean', title: '是否營業' },
        start: { type: 'string', pattern: TIME, title: '開始時間', description: '台北時間' },
        end: { type: 'string', pattern: TIME, title: '結束時間' },
      },
    },
  },
  properties: {
    mon: { $ref: '#/$defs/DayHours', title: '週一' },
    tue: { $ref: '#/$defs/DayHours', title: '週二' },
    wed: { $ref: '#/$defs/DayHours', title: '週三' },
    thu: { $ref: '#/$defs/DayHours', title: '週四' },
    fri: { $ref: '#/$defs/DayHours', title: '週五' },
    sat: { $ref: '#/$defs/DayHours', title: '週六' },
  },
}

const day = (open: boolean, start: string, end: string) => ({ open, start, end })
const SERVICE_HOURS_VALUE = {
  mon: day(true, '12:00', '19:00'),
  tue: day(true, '12:00', '19:00'),
  wed: day(true, '12:00', '19:00'),
  thu: day(true, '12:00', '19:00'),
  fri: day(true, '12:00', '19:00'),
  sat: day(false, '08:00', '12:00'),
}

const PICKUP_WINDOW: JsonSchema = {
  title: '接送時段',
  type: 'object',
  additionalProperties: false,
  required: ['request_start', 'request_end', 'latest_expected_arrival', 'auto_expire_minutes'],
  properties: {
    request_start: { type: 'string', pattern: TIME, title: '可發起接送開始時間' },
    request_end: { type: 'string', pattern: TIME, title: '可發起接送結束時間' },
    latest_expected_arrival: { type: 'string', pattern: TIME, title: '最晚預計抵達時間' },
    auto_expire_minutes: {
      type: 'integer',
      minimum: 10,
      maximum: 600,
      title: '接送請求自動過期分鐘數',
      description: '超過即自動過期',
    },
  },
}

const PICKUP_WINDOW_VALUE = {
  request_start: '12:00',
  request_end: '19:00',
  latest_expected_arrival: '19:00',
  auto_expire_minutes: 120,
}

const NOTIFICATION_TOGGLES = {
  title: '通知開關',
  type: 'object',
  additionalProperties: { type: 'boolean' },
  'x-labels': { 'pickup.requested': '家長發起接送', 'exam.published': '成績發布' },
} as JsonSchema

const LINE_MESSAGING: JsonSchema = {
  title: 'LINE 訊息推播',
  type: 'object',
  additionalProperties: false,
  properties: {
    channel_access_token: {
      anyOf: [{ type: 'string', maxLength: 500 }, { type: 'null' }],
      default: null,
      title: 'LINE 訊息 Channel Access Token',
    },
    channel_secret: {
      anyOf: [{ type: 'string', maxLength: 500 }, { type: 'null' }],
      default: null,
      title: 'LINE 訊息 Channel Secret',
    },
  },
}

const ORG_PROFILE: JsonSchema = {
  title: '安親班資料',
  type: 'object',
  additionalProperties: false,
  required: ['name', 'logo_url'],
  properties: {
    name: { type: 'string', maxLength: 50, title: '安親班名稱', description: '顯示於後台與家長端的名稱' },
    logo_url: {
      anyOf: [{ type: 'string', format: 'uri', minLength: 1, maxLength: 2083 }, { type: 'null' }],
      title: 'Logo 圖片網址',
    },
  },
}

const HOMEWORK_DEFAULTS: JsonSchema = {
  title: '作業進度預設',
  type: 'object',
  additionalProperties: false,
  required: ['auto_reply_without_eta', 'no_eta_reply_text'],
  properties: {
    auto_reply_without_eta: { type: 'boolean', title: '未設定預計時間時自動回覆' },
    no_eta_reply_text: { type: 'string', minLength: 1, maxLength: 100, title: '自動回覆文案' },
  },
}

const LINE_LIFF: JsonSchema = {
  title: 'LINE 登入（LIFF）',
  type: 'object',
  additionalProperties: false,
  required: ['liff_id'],
  properties: {
    liff_id: { type: 'string', pattern: '^$|^\\d+-[A-Za-z0-9]+$', title: 'LIFF 應用程式 ID' },
  },
}

const UNSUPPORTED_MESSAGE = '此欄位格式不支援線上編輯'

type Model = Record<string, unknown>

interface MountOptions {
  /** true：把 emit 的新物件回寫成 modelValue（模擬父層 v-model） */
  vModel?: boolean
}

async function mountForm(props: Record<string, unknown>, opts: MountOptions = {}): Promise<VueWrapper> {
  const holder: { wrapper?: VueWrapper } = {}
  const allProps = opts.vModel
    ? { ...props, 'onUpdate:modelValue': (v: Model) => void holder.wrapper?.setProps({ modelValue: v }) }
    : props
  const mounted = await mountWithApp(JsonSchemaForm, { props: allProps })
  const wrapper = mounted.wrapper as VueWrapper
  holder.wrapper = wrapper
  await settle()
  return wrapper
}

async function settle(): Promise<void> {
  await nextTick()
  await flushPromises()
}

/** el-form-item 的錯誤訊息顯示 / 隱藏有 100ms debounce（refDebounced），斷言錯誤文字前要等過 */
async function settleErrors(): Promise<void> {
  await settle()
  await new Promise((resolve) => setTimeout(resolve, 150))
  await settle()
}

function emitted(wrapper: VueWrapper): Model[] {
  return (wrapper.emitted('update:modelValue') ?? []).map((args) => args[0] as Model)
}

function lastEmitted(wrapper: VueWrapper): Model {
  const last = emitted(wrapper).at(-1)
  if (!last) throw new Error('沒有 emit update:modelValue')
  return last
}

function field(wrapper: VueWrapper, path: string): DOMWrapper<Element> {
  const item = wrapper.find(`[data-test="field-${path}"]`)
  if (!item.exists()) throw new Error(`找不到欄位 ${path}`)
  return item
}

function labelOf(item: DOMWrapper<Element>): string {
  return item.find('.el-form-item__label').text().trim()
}

function validate(wrapper: VueWrapper): boolean {
  return (wrapper.vm as unknown as { validate: () => boolean }).validate()
}

describe('JsonSchemaForm', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('JsonSchemaForm resolves refs and renders nested objects with schema titles', async () => {
    const wrapper = await mountForm({ schema: SERVICE_HOURS, modelValue: SERVICE_HOURS_VALUE })

    const fieldsets = wrapper.findAll('fieldset')
    expect(fieldsets).toHaveLength(6)
    const mon = fieldsets[0]!
    expect(mon.find('legend').text()).toBe('週一')
    expect(fieldsets.map((f) => f.find('legend').text())).toEqual(['週一', '週二', '週三', '週四', '週五', '週六'])

    const items = mon.findAll('.el-form-item')
    expect(items.map(labelOf)).toEqual(['是否營業', '開始時間', '結束時間'])
    expect(field(wrapper, 'mon.open').find('.el-switch').exists()).toBe(true)
    expect(field(wrapper, 'mon.start').find('.el-select').exists()).toBe(true)
    expect(field(wrapper, 'mon.end').find('.el-select').exists()).toBe(true)
    expect(field(wrapper, 'mon.start').find('[data-test=field-tip]').attributes('aria-label')).toBe('台北時間')
    expect(mon.find('[data-test=field-description]').exists()).toBe(false)
  })

  it('JsonSchemaForm emits immutable updates with field paths', async () => {
    const modelValue = { ...PICKUP_WINDOW_VALUE }
    const wrapper = await mountForm({ schema: PICKUP_WINDOW, modelValue })

    const input = field(wrapper, 'auto_expire_minutes').find('input')
    await input.setValue('90')
    await input.trigger('change')
    await settle()

    const first = emitted(wrapper)[0]!
    expect(first.auto_expire_minutes).toBe(90)
    expect(first.request_start).toBe('12:00')
    expect(first).not.toBe(modelValue)
    expect(modelValue.auto_expire_minutes).toBe(120)
  })

  it('JsonSchemaForm emits nested updates without mutating props', async () => {
    const modelValue = structuredClone(SERVICE_HOURS_VALUE)
    const wrapper = await mountForm({ schema: SERVICE_HOURS, modelValue })

    await field(wrapper, 'sat.open').find('.el-switch').trigger('click')
    await settle()

    const first = emitted(wrapper)[0] as typeof SERVICE_HOURS_VALUE
    expect(first.sat).toEqual({ open: true, start: '08:00', end: '12:00' })
    expect(first.mon).toEqual(SERVICE_HOURS_VALUE.mon)
    expect(modelValue.sat.open).toBe(false)
  })

  it('JsonSchemaForm renders boolean map with x-labels', async () => {
    const wrapper = await mountForm({
      schema: NOTIFICATION_TOGGLES,
      modelValue: { 'pickup.requested': true, 'exam.published': false, 'binding.completed': true },
    })

    const toggles = wrapper.findAll('[data-test=toggle]')
    expect(toggles.map((t) => t.find('[data-test=toggle-label]').text())).toEqual([
      '家長發起接送',
      '成績發布',
      'binding.completed',
    ])
    expect(wrapper.findAll('.el-switch')).toHaveLength(3)

    await toggles[1]!.find('.el-switch').trigger('click')
    await settle()

    expect(lastEmitted(wrapper)).toEqual({
      'pickup.requested': true,
      'exam.published': true,
      'binding.completed': true,
    })
  })

  it('JsonSchemaForm handles nullable strings', async () => {
    const wrapper = await mountForm({
      schema: ORG_PROFILE,
      modelValue: { name: '快樂安親班', logo_url: 'https://x/logo.png' },
    })

    const input = field(wrapper, 'logo_url').find('input')
    expect(input.attributes('type')).toBe('url')
    await input.setValue('')
    await settle()

    expect(lastEmitted(wrapper)).toEqual({ name: '快樂安親班', logo_url: null })
  })

  it('JsonSchemaForm shows errors and readonly state', async () => {
    const wrapper = await mountForm({
      schema: SERVICE_HOURS,
      modelValue: SERVICE_HOURS_VALUE,
      errors: { 'mon.start': '開始時間需早於結束時間' },
    })
    await settleErrors()

    expect(field(wrapper, 'mon.start').find('.el-form-item__error').text()).toBe('開始時間需早於結束時間')
    expect(field(wrapper, 'tue.start').find('.el-form-item__error').exists()).toBe(false)

    const ro = await mountForm({ schema: PICKUP_WINDOW, modelValue: PICKUP_WINDOW_VALUE, readonly: true })
    const roInputs = ro.findAll('input')
    expect(roInputs.length).toBeGreaterThan(0)
    for (const i of roInputs) expect(i.attributes('disabled')).toBeDefined()

    const roHours = await mountForm({ schema: SERVICE_HOURS, modelValue: SERVICE_HOURS_VALUE, readonly: true })
    expect(roHours.findAll('.el-switch')).toHaveLength(6)
    for (const s of roHours.findAll('.el-switch')) expect(s.classes()).toContain('is-disabled')
  })

  it('JsonSchemaForm clears field error when edited', async () => {
    const wrapper = await mountForm(
      {
        schema: PICKUP_WINDOW,
        modelValue: PICKUP_WINDOW_VALUE,
        errors: { auto_expire_minutes: '需介於 10~600' },
      },
      { vModel: true },
    )
    await settleErrors()
    expect(field(wrapper, 'auto_expire_minutes').text()).toContain('需介於 10~600')

    const input = field(wrapper, 'auto_expire_minutes').find('input')
    await input.setValue('90')
    await input.trigger('change')
    await settleErrors()

    expect(field(wrapper, 'auto_expire_minutes').text()).not.toContain('需介於 10~600')
  })

  it('JsonSchemaForm marks unsupported shapes', async () => {
    const schema: JsonSchema = {
      type: 'object',
      properties: {
        late_after_minutes: { type: 'integer', minimum: 0, maximum: 120, title: '遲到判定分鐘數' },
        closed_weekdays: { type: 'array', items: { type: 'object' }, title: '固定公休日' },
      },
    }
    const wrapper = await mountForm({
      schema,
      modelValue: { late_after_minutes: 15, closed_weekdays: [{ day: 'sun' }] },
    })

    expect(field(wrapper, 'closed_weekdays').text()).toContain(UNSUPPORTED_MESSAGE)
    expect(field(wrapper, 'closed_weekdays').text()).toContain('[{"day":"sun"}]')

    const input = field(wrapper, 'late_after_minutes').find('input')
    await input.setValue('20')
    await input.trigger('change')
    await settle()

    expect(lastEmitted(wrapper)).toEqual({ late_after_minutes: 20, closed_weekdays: [{ day: 'sun' }] })
  })

  it('JsonSchemaForm falls back to field name without title', async () => {
    const wrapper = await mountForm({
      schema: { type: 'object', properties: { auto_expire_minutes: { type: 'integer' } } },
      modelValue: { auto_expire_minutes: 120 },
    })

    expect(labelOf(field(wrapper, 'auto_expire_minutes'))).toBe('auto_expire_minutes')
  })

  it('JsonSchemaForm renders new registry settings without special handling', async () => {
    const wrapper = await mountForm({
      schema: {
        type: 'object',
        properties: {
          days_before: { type: 'integer', minimum: 0, maximum: 365, title: '可提前申請天數' },
          max_attachments: { type: 'integer', minimum: 0, maximum: 10, title: '每筆附件數上限' },
        },
      },
      modelValue: { days_before: 30, max_attachments: 3 },
    })

    expect(wrapper.findAll('.el-input-number')).toHaveLength(2)
    expect(labelOf(field(wrapper, 'days_before'))).toBe('可提前申請天數')
    expect(labelOf(field(wrapper, 'max_attachments'))).toBe('每筆附件數上限')
    expect(field(wrapper, 'days_before').find('input').attributes('max')).toBe('365')
    expect(field(wrapper, 'max_attachments').find('input').attributes('max')).toBe('10')
  })

  it('JsonSchemaForm time select shows and emits chosen time', async () => {
    const wrapper = await mountForm({ schema: PICKUP_WINDOW, modelValue: PICKUP_WINDOW_VALUE })

    const select = field(wrapper, 'request_end').find('.el-select')
    expect(select.text()).toContain('19:00')
    await select.find('.el-select__wrapper').trigger('click')
    await settle()
    const listId = select.find('input').attributes('aria-controls')
    const options = Array.from(document.getElementById(listId!)?.querySelectorAll<HTMLElement>('.el-select-dropdown__item') ?? [])
    expect(options[0]?.textContent?.trim()).toBe('00:00')
    expect(options.at(-1)?.textContent?.trim()).toBe('23:55')
    options.find((o) => o.textContent?.trim() === '18:30')!.click()
    await settle()

    expect(lastEmitted(wrapper)).toEqual({ ...PICKUP_WINDOW_VALUE, request_end: '18:30' })
  })

  it('JsonSchemaForm renders enum as select', async () => {
    const wrapper = await mountForm({
      schema: {
        type: 'object',
        properties: { mode: { type: 'string', enum: ['auto', 'manual'], title: '模式' } },
      },
      modelValue: { mode: 'auto' },
    })

    expect(field(wrapper, 'mode').find('.el-select').text()).toContain('auto')
  })

  it('JsonSchemaForm passes secret values through unchanged', async () => {
    const wrapper = await mountForm({
      schema: LINE_MESSAGING,
      modelValue: { channel_access_token: '****abcd', channel_secret: null },
      secretFields: ['channel_access_token'],
    })

    const secret = field(wrapper, 'channel_access_token')
    const edit = secret.findAll('button').find((b) => b.text() === '修改')
    if (!edit) throw new Error('找不到「修改」')
    await edit.trigger('click')
    await settle()
    await secret.find('input[type=password]').setValue(' New-Token ')

    const last = lastEmitted(wrapper)
    expect(last).toEqual({ channel_access_token: ' New-Token ', channel_secret: null })

    await wrapper.setProps({ modelValue: last })
    await settle()

    expect(field(wrapper, 'channel_access_token').find('input[type=password]').exists()).toBe(true)
  })

  it('JsonSchemaForm marks required fields only for non-empty strings', async () => {
    const homework = await mountForm({
      schema: HOMEWORK_DEFAULTS,
      modelValue: { auto_reply_without_eta: true, no_eta_reply_text: '已通知老師' },
    })
    expect(field(homework, 'no_eta_reply_text').classes()).toContain('is-required')
    expect(field(homework, 'auto_reply_without_eta').classes()).not.toContain('is-required')

    const pickup = await mountForm({ schema: PICKUP_WINDOW, modelValue: PICKUP_WINDOW_VALUE })
    expect(field(pickup, 'auto_expire_minutes').classes()).not.toContain('is-required')
    expect(field(pickup, 'request_start').classes()).not.toContain('is-required')

    const org = await mountForm({ schema: ORG_PROFILE, modelValue: { name: '快樂安親班', logo_url: null } })
    expect(field(org, 'logo_url').classes()).not.toContain('is-required')
    expect(field(org, 'name').classes()).not.toContain('is-required')
  })

  it('JsonSchemaForm treats circular refs as unsupported', async () => {
    const schema: JsonSchema = {
      type: 'object',
      properties: {
        name: { type: 'string', title: '名稱' },
        node: { $ref: '#/$defs/Node' },
      },
      $defs: {
        Node: { type: 'object', properties: { child: { $ref: '#/$defs/Node' } } },
      },
    }
    const node = { child: { child: null } }
    const wrapper = await mountForm({ schema, modelValue: { name: 'a', node } })

    expect(wrapper.text()).toContain(UNSUPPORTED_MESSAGE)

    await field(wrapper, 'name').find('input').setValue('b')
    await settle()

    expect(lastEmitted(wrapper)).toEqual({ name: 'b', node: { child: { child: null } } })
  })

  it('JsonSchemaForm disables time selects when readonly', async () => {
    const wrapper = await mountForm({ schema: SERVICE_HOURS, modelValue: SERVICE_HOURS_VALUE, readonly: true })

    const timeInputs = wrapper.findAll('fieldset .el-select input')
    expect(timeInputs).toHaveLength(12)
    for (const i of timeInputs) expect(i.attributes('disabled')).toBeDefined()
  })

  it('JsonSchemaForm shows top-level description as text', async () => {
    const wrapper = await mountForm({ schema: PICKUP_WINDOW, modelValue: PICKUP_WINDOW_VALUE })

    const desc = field(wrapper, 'auto_expire_minutes').find('[data-test=field-description]')
    expect(desc.text()).toBe('超過即自動過期')
    expect(wrapper.findAll('[data-test=field-description]')).toHaveLength(1)
  })

  it('JsonSchemaForm validates by schema with chinese messages', async () => {
    const liff = await mountForm({ schema: LINE_LIFF, modelValue: { liff_id: '' } }, { vModel: true })
    const liffInput = field(liff, 'liff_id').find('input')
    await liffInput.setValue('abc')
    await settleErrors()
    expect(liff.text()).not.toContain('格式不正確')
    await liffInput.trigger('blur')
    await settleErrors()
    expect(field(liff, 'liff_id').text()).toContain('格式不正確')
    await liffInput.setValue('1657000000-AbcdEfgh')
    await liffInput.trigger('blur')
    await settleErrors()
    expect(liff.text()).not.toContain('格式不正確')
    expect(validate(liff)).toBe(true)

    const org = await mountForm(
      { schema: ORG_PROFILE, modelValue: { name: '快樂安親班', logo_url: null } },
      { vModel: true },
    )
    await field(org, 'logo_url').find('input').setValue('logo.png')
    await settleErrors()
    expect(validate(org)).toBe(false)
    await settleErrors()
    expect(field(org, 'logo_url').text()).toContain('請輸入有效的網址（例如 https://…）')
    await field(org, 'logo_url').find('input').setValue('https://x/logo.png')
    await settleErrors()
    expect(validate(org)).toBe(true)

    const homework = await mountForm(
      { schema: HOMEWORK_DEFAULTS, modelValue: { auto_reply_without_eta: true, no_eta_reply_text: '已通知老師' } },
      { vModel: true },
    )
    await field(homework, 'no_eta_reply_text').find('input').setValue('  ')
    await settleErrors()
    expect(validate(homework)).toBe(false)
    await settleErrors()
    expect(field(homework, 'no_eta_reply_text').text()).toContain('此欄位不可空白')
    // 不 trim：原樣送出
    expect(lastEmitted(homework).no_eta_reply_text).toBe('  ')
  })

  it('JsonSchemaForm shows fieldset level errors', async () => {
    const wrapper = await mountForm(
      {
        schema: SERVICE_HOURS,
        modelValue: SERVICE_HOURS_VALUE,
        errors: { mon: '營業時，開始時間必須早於結束時間' },
      },
      { vModel: true },
    )

    const mon = wrapper.findAll('fieldset')[0]!
    expect(mon.text()).toContain('營業時，開始時間必須早於結束時間')
    expect(mon.classes()).toContain('is-error')
    expect(wrapper.findAll('fieldset')[1]!.classes()).not.toContain('is-error')

    await field(wrapper, 'mon.open').find('.el-switch').trigger('click')
    await settle()
    expect(wrapper.findAll('fieldset')[0]!.classes()).not.toContain('is-error')
    expect(wrapper.text()).not.toContain('營業時，開始時間必須早於結束時間')

    const clean = await mountForm({ schema: SERVICE_HOURS, modelValue: SERVICE_HOURS_VALUE, errors: {} })
    for (const f of clean.findAll('fieldset')) expect(f.classes()).not.toContain('is-error')
  })
})
