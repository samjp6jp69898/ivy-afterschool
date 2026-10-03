// FRONTEND-046：表單是否有未儲存變更。移植 ivy FE:src/composables/useFormDirty.ts::useFormDirty
// 與 FE:src/composables/useUnsavedChangesGuard.ts::confirmDiscardChanges。
// 以建立時（或 markClean 時）的深拷貝快照與目前值深比較：Date 以時間值比較、陣列順序有意義。
import { ElMessageBox } from 'element-plus'
import { computed, shallowRef, toRaw, unref, type ComputedRef, type Ref } from 'vue'

function isPlainObject(v: unknown): v is Record<string, unknown> {
  if (v === null || typeof v !== 'object') return false
  const proto = Object.getPrototypeOf(v)
  return proto === Object.prototype || proto === null
}

function deepClone<V>(value: V): V {
  const raw = toRaw(value)
  if (raw instanceof Date) return new Date(raw.getTime()) as V
  if (Array.isArray(raw)) return raw.map((item) => deepClone(item)) as V
  if (isPlainObject(raw)) {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(raw)) out[k] = deepClone(v)
    return out as V
  }
  return raw
}

// current 是 reactive 值（讀取時建立依賴），snapshot 是 raw 深拷貝
function deepEqual(current: unknown, snapshot: unknown): boolean {
  if (current instanceof Date || snapshot instanceof Date) {
    return (
      current instanceof Date &&
      snapshot instanceof Date &&
      Object.is(current.getTime(), snapshot.getTime())
    )
  }
  if (Array.isArray(current) || Array.isArray(snapshot)) {
    if (!Array.isArray(current) || !Array.isArray(snapshot)) return false
    if (current.length !== snapshot.length) return false
    return current.every((item, i) => deepEqual(item, snapshot[i]))
  }
  if (isPlainObject(toRaw(current)) && isPlainObject(snapshot)) {
    const obj = current as Record<string, unknown>
    const keys = Object.keys(obj)
    if (keys.length !== Object.keys(snapshot).length) return false
    return keys.every((k) => Object.hasOwn(snapshot, k) && deepEqual(obj[k], snapshot[k]))
  }
  return Object.is(current, snapshot)
}

export function useFormDirty<T extends object>(
  form: T | Ref<T>,
): { isDirty: ComputedRef<boolean>; markClean: () => void } {
  const snapshot = shallowRef(deepClone(unref(form)))

  const isDirty = computed(() => !deepEqual(unref(form), snapshot.value))

  function markClean(): void {
    snapshot.value = deepClone(unref(form))
  }

  return { isDirty, markClean }
}

/** 詢問是否放棄未儲存的變更；使用者選「放棄變更」回 true，「繼續編輯」或關閉對話框回 false。 */
export async function confirmDiscardChanges(): Promise<boolean> {
  try {
    await ElMessageBox.confirm('尚未儲存的變更將會遺失，確定要關閉嗎？', '未儲存的變更', {
      confirmButtonText: '放棄變更',
      cancelButtonText: '繼續編輯',
    })
    return true
  } catch {
    return false
  }
}
