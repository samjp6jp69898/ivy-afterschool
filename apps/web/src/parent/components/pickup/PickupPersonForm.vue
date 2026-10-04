<script setup lang="ts">
/**
 * 新增常用接送人表單（放在 ParentBottomSheet 內，按鈕列屬於本元件）。
 * 內部持有欄位狀態，送出時 emit 正規化後的 payload；不使用 v-model 物件。
 * 照片預覽用 URL.createObjectURL，更換 / 移除 / unmount 時 revoke。
 */
import { computed, onBeforeUnmount, ref, shallowRef, useId } from 'vue'
import M3Button from '../m3/M3Button.vue'
import M3Chip from '../m3/M3Chip.vue'
import M3Icon from '../m3/M3Icon.vue'
import M3TextField from '../m3/M3TextField.vue'

export interface PickupPersonInput {
  name: string
  relation: string
  phone: string
  photo: File | null
}

const props = withDefaults(defineProps<{ submitting?: boolean }>(), { submitting: false })

const emit = defineEmits<{ submit: [input: PickupPersonInput]; cancel: [] }>()

const RELATIONS = ['父親', '母親', '祖父', '祖母', '外公', '外婆', '親戚', '保母', '其他']
const PHOTO_ACCEPT = 'image/jpeg,image/png,image/webp,image/heic'
const MAX_PHOTO_BYTES = 5 * 1024 * 1024
const PHONE_PATTERN = /^0\d{8,9}$/

const relationLabelId = useId()
const photoInputId = useId()
const photoInput = ref<HTMLInputElement | null>(null)

const name = ref('')
const relation = ref('')
const phone = ref('')
const phoneError = ref('')
// File 不可被 reactive 包成 Proxy，payload 要送出原物件
const photo = shallowRef<File | null>(null)
const photoError = ref('')
const previewUrl = ref('')
const previewFailed = ref(false)

const canSubmit = computed(() => name.value.trim() !== '' && relation.value !== '' && phone.value.trim() !== '')

function onPhoneInput(value: string): void {
  phone.value = value
  phoneError.value = ''
}

function releasePreview(): void {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
  previewFailed.value = false
}

function onPhotoChange(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  // 清空 value，才能再次選同一個檔案
  input.value = ''
  if (!file) return
  if (file.size > MAX_PHOTO_BYTES) {
    photoError.value = '照片不可超過 5 MB'
    return
  }
  photoError.value = ''
  releasePreview()
  photo.value = file
  previewUrl.value = URL.createObjectURL(file)
}

function removePhoto(): void {
  releasePreview()
  photo.value = null
  photoError.value = ''
}

function formatSize(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function onSubmit(): void {
  if (props.submitting || !canSubmit.value) return
  const normalized = phone.value.replace(/[\s-]/g, '')
  if (!PHONE_PATTERN.test(normalized)) {
    phoneError.value = '請輸入正確的電話號碼'
    return
  }
  emit('submit', { name: name.value.trim(), relation: relation.value, phone: normalized, photo: photo.value })
}

onBeforeUnmount(releasePreview)
</script>

<template>
  <form
    class="person-form"
    novalidate
    @submit.prevent="onSubmit"
  >
    <M3TextField
      v-model="name"
      label="姓名"
      :maxlength="50"
      autocomplete="off"
      :disabled="submitting"
    />

    <div>
      <p
        :id="relationLabelId"
        class="field-label m3-title-small"
      >
        關係
      </p>
      <div
        class="relation-chips"
        role="radiogroup"
        :aria-labelledby="relationLabelId"
      >
        <M3Chip
          v-for="item in RELATIONS"
          :key="item"
          variant="filter"
          :label="item"
          :selected="relation === item"
          :disabled="submitting"
          role="radio"
          :aria-checked="relation === item ? 'true' : 'false'"
          :aria-pressed="undefined"
          @click="relation = item"
        />
      </div>
    </div>

    <M3TextField
      :model-value="phone"
      label="電話"
      type="tel"
      inputmode="tel"
      autocomplete="off"
      supporting-text="手機或市話，例如 0912-000-123"
      :error-text="phoneError"
      :disabled="submitting"
      @update:model-value="onPhoneInput"
    />

    <div>
      <p class="field-label m3-title-small">
        照片（選填）
      </p>
      <p class="field-help m3-body-small">
        照片可協助老師核對接送人，非必填
      </p>
      <div
        class="photo"
        :class="{ 'is-disabled': submitting }"
      >
        <input
          :id="photoInputId"
          ref="photoInput"
          class="photo__input"
          type="file"
          :accept="PHOTO_ACCEPT"
          :disabled="submitting"
          @change="onPhotoChange"
        >
        <template v-if="photo">
          <div class="photo-tile is-filled">
            <img
              v-if="previewUrl && !previewFailed"
              :src="previewUrl"
              alt="接送人照片預覽"
              @error="previewFailed = true"
            >
            <template v-else>
              <M3Icon name="image" />
              <span class="m3-label-small">無法預覽</span>
            </template>
          </div>
          <div class="photo-meta">
            <p class="photo-meta__name m3-body-small">
              {{ photo.name }} · {{ formatSize(photo.size) }}
            </p>
            <M3Button
              variant="text"
              :disabled="submitting"
              @click="photoInput?.click()"
            >
              更換照片
            </M3Button>
            <M3Button
              class="photo-remove"
              variant="text"
              :disabled="submitting"
              @click="removePhoto"
            >
              移除照片
            </M3Button>
          </div>
        </template>
        <label
          v-else
          class="photo-tile"
          :for="photoInputId"
        >
          <M3Icon name="add_a_photo" />
          <span class="m3-label-small">新增照片</span>
        </label>
      </div>
      <p
        v-if="photoError"
        class="field-error m3-body-small"
        role="alert"
      >
        <M3Icon
          name="error"
          :size="16"
          filled
        />
        {{ photoError }}
      </p>
    </div>

    <div class="form-actions">
      <M3Button
        variant="outlined"
        :disabled="submitting"
        @click="emit('cancel')"
      >
        取消
      </M3Button>
      <M3Button
        type="submit"
        :disabled="!canSubmit"
        :loading="submitting"
      >
        儲存
      </M3Button>
    </div>
  </form>
</template>

<style scoped>
.person-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-top: 8px;
}

.field-label {
  margin: 0 0 4px;
  color: var(--m3-on-surface);
}

.field-help {
  margin: 0;
  color: var(--m3-on-surface-variant);
}

.field-error {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 4px 0 0;
  color: var(--m3-error);
}

.relation-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 0 0;
}

.photo {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 8px;
}

.photo.is-disabled {
  opacity: 0.38;
  pointer-events: none;
}

.photo__input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

.photo-tile {
  position: relative;
  display: inline-flex;
  flex: none;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  box-sizing: border-box;
  width: 96px;
  height: 96px;
  overflow: hidden;
  border: 1px dashed var(--m3-outline);
  border-radius: var(--m3-shape-medium);
  background: var(--m3-surface-container-low);
  color: var(--m3-on-surface-variant);
  cursor: pointer;
}

.photo:focus-within .photo-tile {
  outline: 2px solid var(--m3-primary);
  outline-offset: 2px;
}

.photo-tile.is-filled {
  border-style: solid;
  border-color: var(--m3-outline-variant);
  cursor: default;
}

.photo-tile img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.photo-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  min-width: 0;
}

.photo-meta__name {
  max-width: 100%;
  margin: 0;
  overflow: hidden;
  color: var(--m3-on-surface-variant);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.photo-remove {
  color: var(--m3-error);
}

.form-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.form-actions > * {
  flex: 1 1 0;
}
</style>
