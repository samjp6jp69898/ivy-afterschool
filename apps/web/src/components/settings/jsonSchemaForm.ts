// FRONTEND-055：JsonSchemaForm 的 schema 解析、驗證與不可變更新（純函式）。
// 設計稿：docs/mockups/page-settings-system.html（JsonSchemaForm 段）。
// schema 由後端 Pydantic 產生（BACKEND-106 registry），前端不維護欄位標籤對照表。
import type { JsonSchema } from '@/api/settings'

export type NodeKind =
  | 'object'
  | 'toggles'
  | 'enum'
  | 'time'
  | 'url'
  | 'text'
  | 'number'
  | 'boolean'
  | 'unsupported'

export interface SchemaNode {
  kind: NodeKind
  title?: string
  description?: string
  /** anyOf: [X, {type: null}]：清空時送 null */
  nullable: boolean
  /** 解析 $ref / anyOf 後實際描述值的 schema（min / max / pattern 等從這裡讀） */
  schema: JsonSchema
  /** kind = object */
  fields?: FieldNode[]
  /** kind = toggles：schema['x-labels'] */
  labels?: Record<string, string>
}

export interface FieldNode {
  key: string
  /** 從根到此欄位的 key 序列 */
  segments: string[]
  /** segments 以「.」串接，errors 與 data-test 用 */
  path: string
  label: string
  node: SchemaNode
}

export const TIME_PATTERN = '^([01]\\d|2[0-3]):[0-5]\\d$'
const REF_PREFIX = '#/$defs/'
const URL_RE = /^https?:\/\/\S+$/

const unsupported = (raw: JsonSchema): SchemaNode => ({
  kind: 'unsupported',
  title: raw.title,
  description: raw.description,
  nullable: false,
  schema: raw,
})

function isBooleanMap(raw: JsonSchema): boolean {
  const ap = raw.additionalProperties
  return typeof ap === 'object' && ap.type === 'boolean' && !raw.properties
}

/** seen：目前這條解析路徑上已展開的 $defs 名稱，用來偵測循環引用 */
function buildNode(raw: JsonSchema, root: JsonSchema, seen: string[], segments: string[]): SchemaNode {
  if (raw.$ref !== undefined) {
    const name = raw.$ref.startsWith(REF_PREFIX) ? raw.$ref.slice(REF_PREFIX.length) : null
    const target = name !== null ? root.$defs?.[name] : undefined
    if (name === null || !target || seen.includes(name)) return unsupported(raw)
    const node = buildNode(target, root, [...seen, name], segments)
    // property 自身的 title / description 優先，否則用被引用 model 的
    return { ...node, title: raw.title ?? node.title, description: raw.description ?? node.description }
  }

  if (raw.anyOf) {
    const nonNull = raw.anyOf.filter((s) => s.type !== 'null')
    if (raw.anyOf.length !== 2 || nonNull.length !== 1) return unsupported(raw)
    const node = buildNode(nonNull[0]!, root, seen, segments)
    if (node.kind === 'unsupported') return unsupported(raw)
    return {
      ...node,
      nullable: true,
      title: raw.title ?? node.title,
      description: raw.description ?? node.description,
    }
  }

  const base = { title: raw.title, description: raw.description, nullable: false, schema: raw }

  if (raw.type === 'object' && raw.properties) {
    const fields = Object.entries(raw.properties).map(([key, sub]) => {
      const childSegments = [...segments, key]
      const node = buildNode(sub, root, seen, childSegments)
      return { key, segments: childSegments, path: childSegments.join('.'), label: node.title ?? key, node }
    })
    return { ...base, kind: 'object', fields }
  }
  if (raw.type === 'object' && isBooleanMap(raw)) {
    const labels = (raw as Record<string, unknown>)['x-labels']
    return { ...base, kind: 'toggles', labels: (labels ?? {}) as Record<string, string> }
  }
  if (raw.enum) return { ...base, kind: 'enum' }
  if (raw.type === 'string') {
    if (raw.pattern === TIME_PATTERN) return { ...base, kind: 'time' }
    if (raw.format === 'uri') return { ...base, kind: 'url' }
    return { ...base, kind: 'text' }
  }
  if (raw.type === 'integer' || raw.type === 'number') return { ...base, kind: 'number' }
  if (raw.type === 'boolean') return { ...base, kind: 'boolean' }
  return unsupported(raw)
}

export function resolveSchema(schema: JsonSchema): SchemaNode {
  return buildNode(schema, schema, [], [])
}

const STRING_KINDS: NodeKind[] = ['text', 'url', 'time']

/** 必填星號只標在 minLength ≥ 1 的字串（Pydantic 的 required 只代表 key 必須存在） */
export function isRequired(node: SchemaNode): boolean {
  return STRING_KINDS.includes(node.kind) && !node.nullable && (node.schema.minLength ?? 0) >= 1
}

function patternMatches(pattern: string, value: string): boolean {
  try {
    return new RegExp(pattern).test(value)
  } catch {
    // 後端 regex 語法 JS 不支援時不在前端擋，交給後端驗證
    return true
  }
}

/** 依 schema 驗證單一值，回傳中文錯誤訊息；合法回 '' */
export function validateValue(node: SchemaNode, value: unknown): string {
  if (typeof value !== 'string' || !STRING_KINDS.includes(node.kind)) return ''
  const { minLength, pattern } = node.schema
  if ((minLength ?? 0) >= 1 && value.trim() === '') return '此欄位不可空白'
  if (node.kind === 'url' && value !== '' && !URL_RE.test(value)) return '請輸入有效的網址（例如 https://…）'
  if (pattern && !patternMatches(pattern, value)) return '格式不正確'
  return ''
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

export function getIn(obj: unknown, segments: string[]): unknown {
  let cur = obj
  for (const key of segments) {
    if (!isPlainObject(cur)) return undefined
    cur = cur[key]
  }
  return cur
}

/** 回傳新物件：沿路徑淺拷貝，其餘分支沿用原參照 */
export function setIn(obj: Record<string, unknown>, segments: string[], value: unknown): Record<string, unknown> {
  const [key, ...rest] = segments
  if (key === undefined) return obj
  const child = obj[key]
  return { ...obj, [key]: rest.length ? setIn(isPlainObject(child) ? child : {}, rest, value) : value }
}

/** 驗證所有葉節點（secret 欄位不驗證），回傳 path → 訊息 */
export function validateAll(
  fields: FieldNode[],
  model: Record<string, unknown>,
  secretFields: string[],
): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const f of fields) {
    if (f.node.kind === 'object') {
      Object.assign(errors, validateAll(f.node.fields ?? [], model, secretFields))
      continue
    }
    if (secretFields.includes(f.path)) continue
    const msg = validateValue(f.node, getIn(model, f.segments))
    if (msg) errors[f.path] = msg
  }
  return errors
}

/** 'mon.start' → ['mon.start', 'mon']：修改欄位時一併清除所屬 fieldset 的錯誤 */
export function pathWithAncestors(segments: string[]): string[] {
  return segments.map((_, i) => segments.slice(0, segments.length - i).join('.'))
}
