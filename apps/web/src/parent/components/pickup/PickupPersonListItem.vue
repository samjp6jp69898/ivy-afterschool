<script setup lang="ts">
/**
 * 常用接送人一列（<li>，由頁面放進 <ul>）：頭像、姓名、「關係 · 電話」與刪除鈕。
 * 整列不可點（沒有編輯接送人的功能）；本元件只 emit delete(person)，確認對話框由頁面負責。
 * photo_url 是後端簽發的短效網址，不寫進任何 storage；載入失敗（過期）時退回姓名首字，
 * 失敗以「網址」記錄，重抓清單拿到新網址時會重新嘗試載入。
 */
import { computed, ref } from 'vue'
import type { PickupPerson } from '../../api/pickupPersons'
import M3IconButton from '../m3/M3IconButton.vue'

const props = defineProps<{ person: PickupPerson }>()

defineEmits<{ delete: [person: PickupPerson] }>()

const failedUrl = ref<string | null>(null)

const showPhoto = computed(() => !!props.person.photo_url && props.person.photo_url !== failedUrl.value)

/** 姓名首字；用 Array.from 避免把罕用字（兩個 UTF-16 code unit）切成一半 */
const initial = computed(() => Array.from(props.person.name)[0] ?? '')

function onPhotoError(): void {
  failedUrl.value = props.person.photo_url
}
</script>

<template>
  <li class="ppl">
    <span
      class="ppl__avatar m3-title-medium"
      aria-hidden="true"
    >
      <img
        v-if="showPhoto"
        :src="person.photo_url ?? undefined"
        alt=""
        loading="lazy"
        @error="onPhotoError"
      >
      <template v-else>{{ initial }}</template>
    </span>
    <span class="ppl__text">
      <span class="ppl__name m3-body-large">{{ person.name }}</span>
      <span class="ppl__meta m3-body-medium">{{ person.relation }} · {{ person.phone }}</span>
    </span>
    <M3IconButton
      icon="delete"
      :label="`刪除 ${person.name}`"
      @click="$emit('delete', person)"
    />
  </li>
</template>

<style scoped>
.ppl {
  display: flex;
  align-items: center;
  gap: 16px;
  min-height: 72px;
  padding: 8px 4px 8px 16px;
  list-style: none;
}

/* 列間分隔線由列自己畫 */
.ppl + .ppl {
  border-top: 1px solid var(--m3-outline-variant);
}

.ppl__avatar {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  overflow: hidden;
  border-radius: var(--m3-shape-full);
  background: var(--m3-tertiary-container);
  color: var(--m3-on-tertiary-container);
}

.ppl__avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.ppl__text {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.ppl__name,
.ppl__meta {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ppl__name {
  color: var(--m3-on-surface);
}

.ppl__meta {
  color: var(--m3-on-surface-variant);
  font-variant-numeric: tabular-nums;
}
</style>
