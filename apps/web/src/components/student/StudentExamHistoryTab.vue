<script setup lang="ts">
// FRONTEND-240：學生詳情「成績紀錄」分頁（domain_spec M8：後台可看個人歷次成績列表，不做排名）。
// 設計稿：docs/mockups/page-students.html。每次考試一張卡：日期、考試名稱（連到 /exams/{id}）、類型、
// 草稿加「未發布」（家長尚看不到）；下方列出各科分數，缺考橘字、未填灰字。分頁是否出現由學生詳情依 exams:read 決定。
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { fetchStudentExamHistory, type ExamHistoryItem, type ExamHistoryScore } from '@/api/exams'
import EmptyState from '@/components/common/EmptyState.vue'
import { formatDate } from '@/shared/utils/datetime'
import { errorMessage } from '@/shared/utils/errorMessage'

const props = defineProps<{ studentId: string }>()

const router = useRouter()

const exams = ref<ExamHistoryItem[]>([])
const state = ref<'loading' | 'error' | 'ready'>('loading')
const errorText = ref('')
const examType = ref<string | undefined>(undefined)
// 切換學生時只採用最後一次請求的回應
let seq = 0

/** 類型選項取自資料本身，依出現順序 */
const examTypes = computed(() => [...new Set(exams.value.map((e) => e.exam_type_name))])
const filtered = computed(() => exams.value.filter((e) => !examType.value || e.exam_type_name === examType.value))

async function load(): Promise<void> {
  const mySeq = ++seq
  state.value = 'loading'
  try {
    const items = await fetchStudentExamHistory(props.studentId)
    if (mySeq !== seq) return
    // 依考試日期新到舊；同日維持後端順序
    exams.value = [...items].sort((a, b) => b.exam_date.localeCompare(a.exam_date))
    state.value = 'ready'
  } catch (err) {
    if (mySeq !== seq) return
    errorText.value = errorMessage(err, '伺服器暫時沒有回應，請稍後再試')
    state.value = 'error'
  }
}

watch(
  () => props.studentId,
  () => {
    examType.value = undefined
    void load()
  },
  { immediate: true },
)

function scoreText(s: ExamHistoryScore): string {
  if (s.is_absent) return '缺考'
  if (s.score === null) return '未填'
  return `${s.score} / ${s.full_score}`
}

function scoreClass(s: ExamHistoryScore): string | undefined {
  if (s.is_absent) return 'exam-hist__absent'
  if (s.score === null) return 'exam-hist__missing'
  return undefined
}

function goExam(exam: ExamHistoryItem): void {
  void router.push(`/exams/${encodeURIComponent(exam.exam_id)}`)
}
</script>

<template>
  <div class="exam-history">
    <el-skeleton
      v-if="state === 'loading'"
      :rows="3"
      animated
    />
    <EmptyState
      v-else-if="state === 'error'"
      variant="error"
      title="無法載入成績紀錄"
      :description="errorText"
    >
      <template #action>
        <el-button
          data-test="retry"
          @click="load"
        >
          重試
        </el-button>
      </template>
    </EmptyState>
    <template v-else>
      <div
        v-if="exams.length > 0"
        class="exam-history__toolbar"
      >
        <el-select
          v-model="examType"
          data-test="exam-type-filter"
          class="exam-history__type"
          clearable
          placeholder="全部考試類型"
        >
          <el-option
            v-for="type in examTypes"
            :key="type"
            :label="type"
            :value="type"
          />
        </el-select>
      </div>
      <EmptyState
        v-if="filtered.length === 0"
        variant="inline"
        title="尚無考試紀錄"
      />
      <div
        v-for="exam in filtered"
        :key="exam.exam_id"
        class="exam-hist"
        data-test="exam-history-item"
      >
        <div class="exam-hist__top">
          <span class="exam-hist__date">{{ formatDate(exam.exam_date) }}</span>
          <el-button
            link
            type="primary"
            @click="goExam(exam)"
          >
            {{ exam.exam_name }}
          </el-button>
          <span class="exam-hist__type">{{ exam.exam_type_name }}</span>
          <el-tag
            v-if="exam.status === 'draft'"
            size="small"
            type="info"
            disable-transitions
          >
            未發布
          </el-tag>
        </div>
        <div class="exam-hist__scores">
          <span
            v-for="s in exam.scores"
            :key="s.subject_id"
          >
            {{ s.subject_name }}
            <span :class="scoreClass(s)">{{ scoreText(s) }}</span>
          </span>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.exam-history__toolbar {
  margin-bottom: 12px;
}

.exam-history__type {
  width: 180px;
}

.exam-hist {
  padding: 12px 14px;
  margin-bottom: 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.exam-hist__top {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.exam-hist__date {
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-secondary);
}

.exam-hist__type {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.exam-hist__scores {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  margin-top: 8px;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.exam-hist__absent {
  color: var(--el-color-warning-dark-2);
}

.exam-hist__missing {
  color: var(--el-text-color-placeholder);
}
</style>
