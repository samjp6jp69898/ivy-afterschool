<script setup lang="ts">
// FRONTEND-091：監護人清單、綁定狀態、綁定碼與解除綁定。設計稿：docs/mockups/page-students.html（監護人分頁）。
// 移植 ivy FE:src/components/student/GuardianManager.vue 的清單 + 新增 / 編輯 dialog + 刪除確認 + 綁定碼 dialog + 主要聯絡人標示；
// 去掉 device setup code、revoke devices、custody note、緊急聯絡人、sort_order。窄面板改用卡片列。
// - initial 有值先顯示，掛載後（與切換學生時）仍向後端取最新清單；任一寫入成功後 emit change 並重新載入
//   （主要聯絡人互斥由後端處理，以重新載入反映）。產生、解除綁定、刪除回 409 / 404 表示畫面過時，顯示後端訊息後也重新載入。
// - 綁定碼只在 dialog 開著時保留；dialog 一關就清掉，BindingCodeDialog 同時以 v-if 移除內容。
//   dialog 的監護人與學生姓名取送出請求當下的值：請求中切換學生、回應晚到時，碼仍屬於原學生。
import { Phone, Plus, StarFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, ref, watch } from 'vue'
import {
  deleteGuardian,
  issueBindingCode,
  listGuardians,
  unbindGuardian,
  type Guardian,
} from '@/api/guardians'
import EmptyState from '@/components/common/EmptyState.vue'
import { useConfirmDelete } from '@/composables/useConfirmDelete'
import { PERMISSIONS } from '@/constants/permissions'
import { BINDING_STATUS_META, GUARDIAN_RELATION_LABELS, statusMeta } from '@/shared/constants/statusLabels'
import { isApiError } from '@/shared/types/api'
import { formatDateTime } from '@/shared/utils/datetime'
import { errorMessage } from '@/shared/utils/errorMessage'
import { useAuthStore } from '@/stores/auth'
import BindingCodeDialog from './BindingCodeDialog.vue'
import GuardianFormDialog from './GuardianFormDialog.vue'

const props = withDefaults(
  defineProps<{
    studentId: string
    studentName: string
    initial?: Guardian[]
    /** 學生已封存 */
    readonly?: boolean
  }>(),
  { initial: undefined, readonly: false },
)

const emit = defineEmits<{ change: [] }>()

interface IssuedCode {
  code: string
  expiresAt: string
  guardianName: string
  studentName: string
}

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission(PERMISSIONS.GUARDIANS_WRITE) && !props.readonly)

/** null = 還沒有任何資料（沒有 initial 且尚未載入成功） */
const guardians = ref<Guardian[] | null>(null)
const loadFailed = ref(false)
const busy = ref(false)
const issued = ref<IssuedCode | null>(null)
const formOpen = ref(false)
const editing = ref<Guardian | undefined>(undefined)

let loadSeq = 0

async function load(): Promise<void> {
  const seq = ++loadSeq
  loadFailed.value = false
  try {
    const list = await listGuardians(props.studentId)
    if (seq === loadSeq) guardians.value = list
  } catch (err) {
    if (seq !== loadSeq) return
    if (guardians.value === null) loadFailed.value = true
    else ElMessage.error(errorMessage(err, '無法載入監護人，請稍後再試'))
  }
}

watch(
  () => props.studentId,
  () => {
    guardians.value = props.initial ?? null
    issued.value = null
    void load()
  },
  { immediate: true },
)

const primary = computed(() => guardians.value?.find((g) => g.is_primary) ?? null)
const hasOtherPrimary = computed(() =>
  (guardians.value ?? []).some((g) => g.is_primary && g.id !== editing.value?.id),
)

function bindingText(g: Guardian): string {
  const b = g.binding
  if (b.status === 'bound') return b.parent_display_name ? `已綁定（${b.parent_display_name}）` : '已綁定'
  if (b.status === 'code_issued') return `綁定碼有效至 ${formatDateTime(b.code_expires_at)}`
  return '未綁定'
}

function afterWrite(): void {
  emit('change')
  void load()
}

/** 409 / 404：資料已被別人改過，畫面需要重新載入 */
function isStale(err: unknown): boolean {
  return isApiError(err) && (err.status === 409 || err.status === 404)
}

function onWriteError(err: unknown, fallback: string): void {
  ElMessage.error(errorMessage(err, fallback))
  if (isStale(err)) void load()
}

function openForm(g?: Guardian): void {
  editing.value = g
  formOpen.value = true
}

async function issueCode(g: Guardian): Promise<void> {
  if (g.binding.status === 'code_issued') {
    try {
      await ElMessageBox.confirm('重新產生後舊的綁定碼會立即失效，確定嗎？', '重新產生綁定碼', {
        type: 'warning',
        confirmButtonText: '重新產生',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
  }
  const studentName = props.studentName
  busy.value = true
  try {
    const result = await issueBindingCode(g.id)
    issued.value = { code: result.code, expiresAt: result.expires_at, guardianName: g.name, studentName }
    afterWrite()
  } catch (err) {
    onWriteError(err, '產生綁定碼失敗，請稍後再試')
  } finally {
    busy.value = false
  }
}

function onBindingDialog(open: boolean): void {
  if (!open) issued.value = null
}

async function unbind(g: Guardian): Promise<void> {
  const parentName = g.binding.parent_display_name || g.name
  try {
    await ElMessageBox.confirm(
      `解除後 ${parentName} 將無法在家長端看到 ${props.studentName}，確定要解除嗎？`,
      '解除綁定',
      { type: 'warning', confirmButtonText: '解除綁定', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  busy.value = true
  try {
    await unbindGuardian(g.id)
    ElMessage.success('已解除綁定')
    afterWrite()
  } catch (err) {
    onWriteError(err, '解除綁定失敗，請稍後再試')
  } finally {
    busy.value = false
  }
}

const { confirmDelete, deleting } = useConfirmDelete<Guardian>({
  // 失敗訊息由 useConfirmDelete 顯示；409 / 404 另外重新載入
  request: (g) =>
    deleteGuardian(g.id).catch((err: unknown) => {
      if (isStale(err)) void load()
      throw err
    }),
  confirmMessage: (g) =>
    `確定要刪除 ${g.name} 嗎？${
      g.binding.status === 'bound' ? '此監護人已綁定家長帳號，刪除後家長將無法再看到此學生。' : ''
    }`,
  onSuccess: afterWrite,
})
</script>

<template>
  <div class="guardian-manager">
    <div class="guardian-toolbar">
      <el-tag
        v-if="primary"
        type="success"
        size="small"
        disable-transitions
      >
        主要聯絡人：{{ primary.name }}
      </el-tag>
      <el-tag
        v-else-if="guardians?.length"
        type="warning"
        size="small"
        disable-transitions
      >
        尚未設定主要聯絡人
      </el-tag>
      <span v-else />
      <el-button
        v-if="canWrite"
        type="primary"
        :icon="Plus"
        @click="openForm()"
      >
        新增監護人
      </el-button>
    </div>

    <el-skeleton
      v-if="guardians === null && !loadFailed"
      :rows="4"
      animated
    />
    <EmptyState
      v-else-if="guardians === null"
      variant="error"
      title="無法載入監護人"
      description="伺服器暫時沒有回應，請稍後再試"
    >
      <template #action>
        <el-button @click="load">
          重試
        </el-button>
      </template>
    </EmptyState>
    <EmptyState
      v-else-if="!guardians.length"
      variant="inline"
      title="尚未建立監護人"
    >
      <template
        v-if="canWrite"
        #action
      >
        <el-button
          size="small"
          type="primary"
          @click="openForm()"
        >
          新增監護人
        </el-button>
      </template>
    </EmptyState>
    <template v-else>
      <article
        v-for="g in guardians"
        :key="g.id"
        class="guardian-card"
      >
        <div class="guardian-card__top">
          <span class="guardian-card__name">{{ g.name }}</span>
          <span class="guardian-card__relation">{{ GUARDIAN_RELATION_LABELS[g.relation] ?? g.relation }}</span>
          <span
            v-if="g.is_primary"
            class="guardian-card__primary"
          ><el-icon><StarFilled /></el-icon>主要</span>
        </div>
        <div class="guardian-card__meta">
          <span class="guardian-card__phone"><el-icon><Phone /></el-icon> {{ g.phone || '—' }}</span>
          <span
            class="guardian-card__flag"
            :class="{ 'is-off': !g.can_pickup }"
          >可接送 {{ g.can_pickup ? '✓' : '—' }}</span>
          <span
            class="guardian-card__flag"
            :class="{ 'is-off': !g.receives_notifications }"
          >收通知 {{ g.receives_notifications ? '✓' : '—' }}</span>
        </div>
        <div class="guardian-card__binding">
          <el-tag
            :type="statusMeta(BINDING_STATUS_META, g.binding.status).tone"
            disable-transitions
          >
            {{ bindingText(g) }}
          </el-tag>
        </div>
        <div
          v-if="canWrite"
          class="guardian-card__actions"
        >
          <el-button
            link
            type="primary"
            :disabled="busy || deleting"
            @click="openForm(g)"
          >
            編輯
          </el-button>
          <el-button
            v-if="g.binding.status !== 'bound'"
            link
            type="primary"
            :disabled="busy || deleting"
            @click="issueCode(g)"
          >
            {{ g.binding.status === 'code_issued' ? '重新產生綁定碼' : '產生綁定碼' }}
          </el-button>
          <el-button
            v-else
            link
            type="warning"
            :disabled="busy || deleting"
            @click="unbind(g)"
          >
            解除綁定
          </el-button>
          <el-button
            link
            type="danger"
            :disabled="busy || deleting"
            @click="confirmDelete(g)"
          >
            刪除
          </el-button>
        </div>
      </article>
    </template>

    <GuardianFormDialog
      v-model="formOpen"
      :student-id="studentId"
      :guardian="editing"
      :has-other-primary="hasOtherPrimary"
      @saved="afterWrite"
    />
    <BindingCodeDialog
      :model-value="issued !== null"
      :code="issued?.code ?? ''"
      :expires-at="issued?.expiresAt ?? ''"
      :guardian-name="issued?.guardianName ?? ''"
      :student-name="issued?.studentName ?? ''"
      @update:model-value="onBindingDialog"
    />
  </div>
</template>

<style scoped>
.guardian-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.guardian-card {
  padding: 12px 14px;
  margin-bottom: 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.guardian-card__top {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.guardian-card__name {
  font-size: 15px;
  font-weight: 600;
}

.guardian-card__relation {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.guardian-card__primary {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  font-size: 12px;
  color: var(--el-color-warning-dark-2);
}

.guardian-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 6px;
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.guardian-card__phone {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.guardian-card__flag.is-off {
  color: var(--el-text-color-placeholder);
}

.guardian-card__binding {
  margin-top: 8px;
}

.guardian-card__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding-top: 8px;
  margin-top: 8px;
  border-top: 1px dashed var(--el-border-color-lighter);
}

.guardian-card__actions :deep(.el-button + .el-button) {
  margin-left: 0;
}
</style>
