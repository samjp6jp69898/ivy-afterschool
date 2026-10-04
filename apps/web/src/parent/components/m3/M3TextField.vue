<script setup lang="ts">
/**
 * M3 outlined 文字欄位。浮動 label 以 fieldset / legend 在邊框挖缺口，放在任何底色上都不用補底色。
 * errorText 非空即為錯誤態（取代 supportingText）；有 maxlength 就顯示字數。
 */
import { computed, ref, useId, type HTMLAttributes } from 'vue'
import M3Icon from './M3Icon.vue'

const props = withDefaults(
  defineProps<{
    modelValue?: string
    label: string
    type?: 'text' | 'tel' | 'date' | 'time' | 'textarea'
    placeholder?: string
    supportingText?: string
    errorText?: string
    disabled?: boolean
    maxlength?: number
    inputmode?: HTMLAttributes['inputmode']
    autocomplete?: string
    id?: string
  }>(),
  {
    modelValue: '',
    type: 'text',
    placeholder: undefined,
    supportingText: '',
    errorText: '',
    disabled: false,
    maxlength: undefined,
    inputmode: undefined,
    autocomplete: undefined,
    id: undefined,
  },
)

const emit = defineEmits<{ 'update:modelValue': [string]; enter: [] }>()

const generatedId = useId()
const focused = ref(false)

const inputId = computed(() => props.id ?? `m3-tf-${generatedId}`)
const descId = computed(() => `${inputId.value}-desc`)
const hasError = computed(() => props.errorText !== '')
const supportText = computed(() => props.errorText || props.supportingText)
const isTextarea = computed(() => props.type === 'textarea')
// date / time 的原生控制項本身會顯示格式字，label 一律浮起避免重疊
const floating = computed(
  () => focused.value || props.modelValue !== '' || props.type === 'date' || props.type === 'time',
)
const showCounter = computed(() => props.maxlength !== undefined)
const shownPlaceholder = computed(() => (focused.value ? props.placeholder : undefined))

const fieldAttrs = computed(() => ({
  id: inputId.value,
  class: 'm3-tf__input',
  value: props.modelValue,
  placeholder: shownPlaceholder.value,
  disabled: props.disabled,
  maxlength: props.maxlength,
  'aria-invalid': hasError.value ? ('true' as const) : ('false' as const),
  'aria-describedby': supportText.value ? descId.value : undefined,
}))

function onInput(event: Event): void {
  emit('update:modelValue', (event.target as HTMLInputElement | HTMLTextAreaElement).value)
}

function onKeydown(event: KeyboardEvent): void {
  // 注音等輸入法組字中按 Enter 是選字，不是送出
  if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return
  emit('enter')
}
</script>

<template>
  <div
    class="m3-tf"
    :class="{
      'is-focused': focused,
      'is-floating': floating,
      'is-error': hasError,
      'is-disabled': disabled,
      'is-textarea': isTextarea,
    }"
  >
    <div class="m3-tf__control">
      <textarea
        v-if="isTextarea"
        v-bind="fieldAttrs"
        rows="4"
        @input="onInput"
        @focus="focused = true"
        @blur="focused = false"
      />
      <input
        v-else
        v-bind="fieldAttrs"
        :type="type"
        :inputmode="inputmode"
        :autocomplete="autocomplete"
        @input="onInput"
        @keydown="onKeydown"
        @focus="focused = true"
        @blur="focused = false"
      >
      <label
        class="m3-tf__label"
        :for="inputId"
      >{{ label }}</label>
      <fieldset
        class="m3-tf__outline"
        aria-hidden="true"
      >
        <legend><span>{{ label }}</span></legend>
      </fieldset>
      <M3Icon
        v-if="hasError"
        class="m3-tf__trailing"
        name="error"
        filled
      />
    </div>
    <div
      v-if="supportText || showCounter"
      class="m3-tf__supporting m3-body-small"
    >
      <span
        v-if="supportText"
        :id="descId"
        class="m3-tf__supporting-text"
      >{{ supportText }}</span>
      <span
        v-if="showCounter"
        class="m3-tf__counter"
      >{{ modelValue.length }}/{{ maxlength }}</span>
    </div>
  </div>
</template>

<style scoped>
.m3-tf {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
}

.m3-tf__control {
  position: relative;
  display: flex;
  align-items: center;
  min-height: 56px;
}

.m3-tf.is-textarea .m3-tf__control {
  align-items: stretch;
}

.m3-tf__input {
  position: relative;
  z-index: 1;
  width: 100%;
  height: 56px;
  margin: 0;
  padding: 0 16px;
  border: none;
  outline: none;
  background: transparent;
  color: var(--m3-on-surface);
  font: 400 16px/24px var(--m3-font);
  letter-spacing: 0.5px;
  caret-color: var(--m3-primary);
  -webkit-appearance: none;
  appearance: none;
}

textarea.m3-tf__input {
  display: block;
  height: auto;
  padding: 16px;
  resize: none;
}

.m3-tf__input::placeholder {
  color: var(--m3-on-surface-variant);
}

.m3-tf.is-error .m3-tf__input {
  padding-right: 48px;
  caret-color: var(--m3-error);
}

.m3-tf__label {
  position: absolute;
  z-index: 2;
  top: 28px;
  left: 16px;
  max-width: calc(100% - 32px);
  overflow: hidden;
  color: var(--m3-on-surface-variant);
  font: 400 16px/24px var(--m3-font);
  text-overflow: ellipsis;
  white-space: nowrap;
  pointer-events: none;
  transform: translateY(-50%);
  transition:
    top var(--m3-dur-short-3) var(--m3-easing-standard),
    font-size var(--m3-dur-short-3) var(--m3-easing-standard),
    line-height var(--m3-dur-short-3) var(--m3-easing-standard);
}

.m3-tf.is-floating .m3-tf__label {
  top: 0;
  font-size: 12px;
  line-height: 16px;
}

.m3-tf.is-focused .m3-tf__label {
  color: var(--m3-primary);
}

.m3-tf.is-error .m3-tf__label {
  color: var(--m3-error);
}

.m3-tf__outline {
  position: absolute;
  z-index: 0;
  inset: -5px 0 0;
  min-width: 0;
  margin: 0;
  padding: 0 12px;
  border: 1px solid var(--m3-outline);
  border-radius: var(--m3-shape-extra-small);
  text-align: left;
  pointer-events: none;
}

/* legend 撐出 label 寬度的缺口；未浮起時寬度為 0 */
.m3-tf__outline legend {
  display: block;
  float: none;
  width: auto;
  max-width: 0.01px;
  height: 11px;
  padding: 0;
  overflow: hidden;
  font-size: 12px;
  white-space: nowrap;
  visibility: hidden;
}

.m3-tf__outline legend span {
  display: inline-block;
  padding: 0 4px;
  opacity: 0;
}

.m3-tf.is-floating .m3-tf__outline legend {
  max-width: 100%;
}

.m3-tf__control:hover .m3-tf__outline {
  border-color: var(--m3-on-surface);
}

/* 焦點指示：2px primary 邊框 */
.m3-tf.is-focused .m3-tf__outline {
  padding: 0 11px;
  border-width: 2px;
  border-color: var(--m3-primary);
}

.m3-tf.is-error .m3-tf__outline {
  border-color: var(--m3-error);
}

.m3-tf__trailing {
  position: absolute;
  z-index: 1;
  top: 16px;
  right: 12px;
  color: var(--m3-error);
}

.m3-tf__supporting {
  display: flex;
  gap: 16px;
  padding: 0 16px;
  color: var(--m3-on-surface-variant);
}

.m3-tf__supporting-text {
  flex: 1;
}

.m3-tf.is-error .m3-tf__supporting-text {
  color: var(--m3-error);
}

.m3-tf__counter {
  flex: none;
  margin-left: auto;
}

.m3-tf.is-disabled {
  opacity: 0.38;
}

.m3-tf.is-disabled .m3-tf__control {
  pointer-events: none;
}
</style>
