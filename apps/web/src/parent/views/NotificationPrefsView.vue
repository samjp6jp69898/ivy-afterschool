<script setup lang="ts">
/**
 * LINE 通知設定（/notifications/preferences）。項目與名稱由後端提供，前端不寫事件對照。
 * 切換為樂觀更新、只送該項；成功以回應覆蓋（仍在儲存中的其他項保留本地值，避免先回來的回應蓋掉它們），
 * 失敗回滾該項。同一項儲存中再點忽略（M3Switch busy 不 emit），不同項可同時儲存。
 */
import { onMounted, reactive, ref } from 'vue'
import {
  getNotificationPreferences,
  updateNotificationPreferences,
  type PreferenceItem,
} from '../api/notificationPreferences'
import M3Card from '../components/m3/M3Card.vue'
import M3Icon from '../components/m3/M3Icon.vue'
import M3Switch from '../components/m3/M3Switch.vue'
import ParentEmptyState from '../components/ParentEmptyState.vue'
import SkeletonBlock from '../components/SkeletonBlock.vue'
import { useSnackbarStore } from '../stores/snackbar'
import { notificationEventIcon } from '../utils/notificationEventIcon'

const snackbar = useSnackbarStore()

const state = ref<'loading' | 'ready' | 'error'>('loading')
const items = ref<PreferenceItem[]>([])
const saving = reactive(new Set<string>())

async function load(): Promise<void> {
  state.value = 'loading'
  try {
    items.value = await getNotificationPreferences()
    state.value = 'ready'
  } catch {
    state.value = 'error'
  }
}

function setLocal(event: string, lineEnabled: boolean): void {
  const item = items.value.find((i) => i.event === event)
  if (item) item.line_enabled = lineEnabled
}

async function toggle(item: PreferenceItem, lineEnabled: boolean): Promise<void> {
  const { event } = item
  if (saving.has(event)) return
  const previous = item.line_enabled
  item.line_enabled = lineEnabled
  saving.add(event)
  try {
    const updated = await updateNotificationPreferences([{ event, line_enabled: lineEnabled }])
    const pending = new Map(
      items.value.filter((i) => i.event !== event && saving.has(i.event)).map((i) => [i.event, i.line_enabled]),
    )
    items.value = updated.map((i) => (pending.has(i.event) ? { ...i, line_enabled: pending.get(i.event)! } : i))
  } catch {
    setLocal(event, previous)
    snackbar.show('儲存失敗，請稍後再試', { tone: 'error' })
  } finally {
    saving.delete(event)
  }
}

onMounted(load)
</script>

<template>
  <div class="prefs-view">
    <p
      v-if="state !== 'error'"
      class="prefs-intro m3-body-medium"
    >
      關閉後仍會在 App 的通知中看到，只是不會傳 LINE 訊息。
    </p>

    <M3Card
      v-if="state === 'ready'"
      variant="outlined"
      padding="none"
      class="prefs-card"
    >
      <ul
        class="prefs-list"
        role="list"
        aria-label="LINE 通知"
      >
        <li
          v-for="item in items"
          :key="item.event"
          class="pref-row"
          :class="{ 'is-off': !item.line_enabled }"
        >
          <!-- 整列是 label：點列上任何位置都切換該項 -->
          <label class="pref-row__label">
            <span
              class="pref-row__icon"
              aria-hidden="true"
            >
              <M3Icon :name="notificationEventIcon(item.event)" />
            </span>
            <span class="pref-row__text m3-body-large">{{ item.label }}</span>
            <M3Switch
              :model-value="item.line_enabled"
              :label="item.label"
              :busy="saving.has(item.event)"
              @update:model-value="toggle(item, $event)"
            />
          </label>
        </li>
      </ul>
    </M3Card>
    <M3Card
      v-else-if="state === 'loading'"
      variant="outlined"
      padding="none"
      class="prefs-card"
    >
      <SkeletonBlock
        variant="row"
        :count="7"
      />
    </M3Card>
    <ParentEmptyState
      v-else
      variant="error"
      title="通知設定載入失敗"
      @action="load"
    />

    <p
      v-if="state === 'ready'"
      class="prefs-footnote m3-body-small"
    >
      接送取消、綁定完成等重要通知不受此設定影響，一律會傳 LINE。
    </p>
  </div>
</template>

<style scoped>
.prefs-view {
  padding: 0 16px 24px;
}

.prefs-intro {
  margin: 8px 4px 12px;
  color: var(--m3-on-surface-variant);
}

.prefs-card {
  overflow: hidden;
}

.prefs-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.pref-row + .pref-row {
  border-top: 1px solid var(--m3-outline-variant);
}

.pref-row__label {
  position: relative;
  display: flex;
  align-items: center;
  gap: 16px;
  min-height: 64px;
  padding: 8px 8px 8px 16px;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

/* M3 state layer */
.pref-row__label::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--m3-on-surface);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--m3-dur-short-2) var(--m3-easing-standard);
}

.pref-row__label:hover::before {
  opacity: var(--m3-state-hover);
}

.pref-row__label:active::before {
  opacity: var(--m3-state-pressed);
}

.pref-row__icon {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}

.pref-row.is-off .pref-row__icon {
  background: var(--m3-surface-container-high);
  color: var(--m3-on-surface-variant);
}

.pref-row__text {
  flex: 1;
  min-width: 0;
  color: var(--m3-on-surface);
  overflow-wrap: anywhere;
}

.prefs-footnote {
  margin: 12px 4px 0;
  color: var(--m3-on-surface-variant);
}
</style>
