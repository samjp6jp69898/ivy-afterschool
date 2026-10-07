<script setup lang="ts">
/**
 * 目前小孩的身分卡：單一小孩只顯示；多位小孩時整張可點，開 ParentBottomSheet「選擇小孩」。
 * 本元件不打 API：選另一位小孩只 emit select(id)，各頁依 children.selectedId 自行重新載入。
 * 頭像照片是短效網址，載入失敗（過期）時退回姓名首字；失敗以「網址」記錄，
 * 重抓清單拿到新網址時會重新嘗試載入。
 */
import { computed, reactive, ref, watch } from 'vue'
import { STUDENT_STATUS_META } from '@/shared/constants/statusLabels'
import type { ChildSummary } from '../api/children'
import M3Icon from './m3/M3Icon.vue'
import ParentBottomSheet from './ParentBottomSheet.vue'
import StatusPill from './StatusPill.vue'

// 多位小孩時根節點是 Fragment（身分卡 + sheet），attrs 明確綁到身分卡
defineOptions({ inheritAttrs: false })

const props = defineProps<{ children: ChildSummary[]; selectedId: string | null }>()

const emit = defineEmits<{ select: [id: string] }>()

const SUSPENDED = STUDENT_STATUS_META.suspended

const open = ref(false)
const failedPhotos = reactive(new Set<string>())

// selectedId 找不到對應小孩（例如剛解除綁定）時以第一位顯示，store 會在下次載入修正 selectedId
const current = computed(() => props.children.find((c) => c.id === props.selectedId) ?? props.children[0] ?? null)
const multi = computed(() => props.children.length > 1)

// 小孩縮到一位時關掉 sheet，避免之後清單變多又自己打開
watch(multi, (value) => {
  if (!value) open.value = false
})

function subtitle(c: ChildSummary): string {
  return [`${c.grade_level} 年級`, c.class_name].filter(Boolean).join(' · ')
}

/** 姓名首字；用 Array.from 避免把罕用字（兩個 UTF-16 code unit）切成一半 */
function initial(c: ChildSummary): string {
  return Array.from(c.name)[0] ?? ''
}

function hasPhoto(c: ChildSummary): boolean {
  return !!c.photo_url && !failedPhotos.has(c.photo_url)
}

function onPhotoError(c: ChildSummary): void {
  if (c.photo_url) failedPhotos.add(c.photo_url)
}

function openSheet(): void {
  if (multi.value) open.value = true
}

function pick(id: string): void {
  if (id !== current.value?.id) emit('select', id)
  open.value = false
}
</script>

<template>
  <template v-if="current">
    <component
      :is="multi ? 'button' : 'div'"
      v-bind="$attrs"
      :type="multi ? 'button' : undefined"
      class="cch"
      :class="{ 'is-interactive': multi }"
      :aria-haspopup="multi ? 'dialog' : undefined"
      @click="openSheet"
    >
      <span
        class="cch__avatar m3-title-medium"
        aria-hidden="true"
      >
        <img
          v-if="hasPhoto(current)"
          :src="current.photo_url ?? undefined"
          alt=""
          @error="onPhotoError(current)"
        >
        <template v-else>{{ initial(current) }}</template>
      </span>
      <span class="cch__text">
        <span class="cch__name-row">
          <span class="cch__name m3-title-medium">{{ current.name }}</span>
          <StatusPill
            v-if="current.status === 'suspended'"
            :label="SUSPENDED.label"
            :tone="SUSPENDED.tone"
          />
        </span>
        <span class="cch__sub m3-body-medium">{{ subtitle(current) }}</span>
        <span
          v-if="current.school_name"
          class="cch__school m3-body-small"
        >{{ current.school_name }}</span>
      </span>
      <template v-if="multi">
        <span class="visually-hidden">，切換小孩</span>
        <M3Icon
          class="cch__chevron"
          name="expand_more"
        />
      </template>
    </component>
    <ParentBottomSheet
      v-if="multi"
      v-model="open"
      title="選擇小孩"
    >
      <ul class="cch-list">
        <li
          v-for="c in children"
          :key="c.id"
        >
          <button
            type="button"
            class="cch-option"
            :class="{ 'is-selected': c.id === current.id }"
            :aria-current="c.id === current.id ? 'true' : undefined"
            @click="pick(c.id)"
          >
            <span
              class="cch-option__avatar m3-title-small"
              aria-hidden="true"
            >
              <img
                v-if="hasPhoto(c)"
                :src="c.photo_url ?? undefined"
                alt=""
                @error="onPhotoError(c)"
              >
              <template v-else>{{ initial(c) }}</template>
            </span>
            <span class="cch-option__text">
              <span class="cch__name-row">
                <span class="cch-option__name m3-body-large">{{ c.name }}</span>
                <StatusPill
                  v-if="c.status === 'suspended'"
                  :label="SUSPENDED.label"
                  :tone="SUSPENDED.tone"
                />
              </span>
              <span class="cch-option__sub m3-body-medium">{{ subtitle(c) }}</span>
            </span>
            <M3Icon
              v-if="c.id === current.id"
              class="cch-option__check"
              name="check"
            />
          </button>
        </li>
      </ul>
    </ParentBottomSheet>
  </template>
</template>

<style scoped>
/* 小孩身分卡；多位小孩時整張可點 */
.cch {
  position: relative;
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
  min-height: 80px;
  margin: 0;
  padding: 12px 16px;
  border: none;
  border-radius: var(--m3-shape-large);
  background: var(--m3-surface-container-low);
  color: var(--m3-on-surface);
  font: inherit;
  text-align: left;
}

.cch.is-interactive {
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

/* M3 state layer */
.cch.is-interactive::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: currentcolor;
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--m3-dur-short-2) var(--m3-easing-standard);
}

.cch.is-interactive:hover::before {
  opacity: var(--m3-state-hover);
}

.cch.is-interactive:focus-visible::before {
  opacity: var(--m3-state-focus);
}

.cch.is-interactive:active::before {
  opacity: var(--m3-state-pressed);
}

.cch.is-interactive:focus-visible {
  outline: 2px solid var(--m3-primary);
  outline-offset: 2px;
}

.cch__avatar,
.cch-option__avatar {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  border-radius: var(--m3-shape-full);
  background: var(--m3-tertiary-container);
  color: var(--m3-on-tertiary-container);
}

.cch__avatar {
  width: 48px;
  height: 48px;
}

.cch__avatar img,
.cch-option__avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cch__text,
.cch-option__text {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.cch__name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

/* 暫停膠囊不被長姓名擠扁 */
.cch__name-row .status-pill {
  flex: none;
}

.cch__name,
.cch__sub,
.cch__school,
.cch-option__name,
.cch-option__sub {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cch__sub,
.cch__school {
  color: var(--m3-on-surface-variant);
}

.cch__chevron {
  flex: none;
  color: var(--m3-on-surface-variant);
}

/* 「選擇小孩」sheet 內清單 */
.cch-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0 -12px;
  padding: 0 0 8px;
  list-style: none;
}

.cch-option {
  position: relative;
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
  min-height: 64px;
  padding: 8px 16px;
  border: none;
  border-radius: var(--m3-shape-large);
  background: transparent;
  color: var(--m3-on-surface);
  font: inherit;
  text-align: left;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.cch-option::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: currentcolor;
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--m3-dur-short-2) var(--m3-easing-standard);
}

.cch-option:hover::before {
  opacity: var(--m3-state-hover);
}

.cch-option:focus-visible::before {
  opacity: var(--m3-state-focus);
}

.cch-option:active::before {
  opacity: var(--m3-state-pressed);
}

.cch-option:focus-visible {
  outline: 2px solid var(--m3-primary);
  outline-offset: -2px;
}

.cch-option.is-selected {
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}

.cch-option__avatar {
  width: 40px;
  height: 40px;
}

.cch-option__sub {
  color: var(--m3-on-surface-variant);
}

.cch-option.is-selected .cch-option__sub {
  color: var(--m3-on-secondary-container);
}

.cch-option__check {
  flex: none;
  color: var(--m3-primary);
}

.cch-option.is-selected .cch-option__check {
  color: var(--m3-on-secondary-container);
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  border: 0;
  white-space: nowrap;
  clip: rect(0 0 0 0);
}
</style>
