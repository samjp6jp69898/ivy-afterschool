// FRONTEND-083：班級 / 科目 / 考試類型 / 國小的下拉快取，各頁篩選與表單共用，避免每頁重抓。
// ensure 只抓未載入的種類，同種類併發共用一個 promise；資料異動後由異動方 invalidate。
import { defineStore } from 'pinia'
import { computed, reactive, shallowRef } from 'vue'
import { listClasses, type ClassItem } from '@/api/classes'
import { examTypesApi, schoolsApi, subjectsApi, type ExamType, type School, type Subject } from '@/api/referenceData'

export type LookupKind = 'classes' | 'subjects' | 'examTypes' | 'schools'

export interface LookupOption {
  label: string
  value: string
}

/** 學年度新到舊，同學年度依 sort_order */
function sortClasses(items: ClassItem[]): ClassItem[] {
  return [...items].sort((a, b) => b.academic_year - a.academic_year || a.sort_order - b.sort_order)
}

export const useLookupsStore = defineStore('lookups', () => {
  const classes = shallowRef<ClassItem[]>([])
  const subjects = shallowRef<Subject[]>([])
  const examTypes = shallowRef<ExamType[]>([])
  const schools = shallowRef<School[]>([])
  const loaded = reactive<Record<LookupKind, boolean>>({
    classes: false,
    subjects: false,
    examTypes: false,
    schools: false,
  })

  const pending = new Map<LookupKind, Promise<void>>()
  // invalidate 時遞增；載入中途被 invalidate 的結果不寫回、也不標成已載入
  const generation: Record<LookupKind, number> = { classes: 0, subjects: 0, examTypes: 0, schools: 0 }

  /** 抓資料後回傳寫回函式，由 load 依 generation 決定是否寫回 */
  const fetchers: Record<LookupKind, () => Promise<() => void>> = {
    classes: async () => {
      const items = sortClasses(await listClasses({ include_archived: false }))
      return () => {
        classes.value = items
      }
    },
    subjects: async () => {
      const items = await subjectsApi.list({ active_only: true })
      return () => {
        subjects.value = items
      }
    },
    examTypes: async () => {
      const items = await examTypesApi.list({ active_only: true })
      return () => {
        examTypes.value = items
      }
    },
    schools: async () => {
      const items = await schoolsApi.list({ active_only: true })
      return () => {
        schools.value = items
      }
    },
  }

  function load(kind: LookupKind): Promise<void> {
    const inFlight = pending.get(kind)
    if (inFlight) return inFlight
    const gen = generation[kind]
    const p = fetchers[kind]()
      .then((commit) => {
        if (gen !== generation[kind]) return
        commit()
        loaded[kind] = true
      })
      .finally(() => {
        if (pending.get(kind) === p) pending.delete(kind)
      })
    pending.set(kind, p)
    return p
  }

  /** 失敗時該種類維持未載入並 reject */
  async function ensure(...kinds: LookupKind[]): Promise<void> {
    await Promise.all(kinds.filter((k) => !loaded[k]).map(load))
  }

  function invalidate(kind: LookupKind): void {
    generation[kind] += 1
    pending.delete(kind)
    loaded[kind] = false
  }

  function classOptions(academicYear?: number): LookupOption[] {
    return classes.value
      .filter((c) => academicYear === undefined || c.academic_year === academicYear)
      .map((c) => ({ label: c.name, value: c.id }))
  }

  const subjectOptions = computed<LookupOption[]>(() => subjects.value.map((s) => ({ label: s.name, value: s.id })))
  const examTypeOptions = computed<LookupOption[]>(() => examTypes.value.map((t) => ({ label: t.name, value: t.id })))
  const schoolOptions = computed<LookupOption[]>(() =>
    schools.value.map((s) => ({ label: s.short_name || s.name, value: s.id })),
  )

  return {
    classes,
    subjects,
    examTypes,
    schools,
    loaded,
    classesLoaded: computed(() => loaded.classes),
    subjectsLoaded: computed(() => loaded.subjects),
    examTypesLoaded: computed(() => loaded.examTypes),
    schoolsLoaded: computed(() => loaded.schools),
    classOptions,
    subjectOptions,
    examTypeOptions,
    schoolOptions,
    ensure,
    invalidate,
  }
})
