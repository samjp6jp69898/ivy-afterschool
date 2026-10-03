<script setup lang="ts">
/**
 * Material Symbols Rounded ligature 圖示。字型與 1em 夾盒在 styles/icons.css。
 * label 有值 → role="img" + aria-label；無值 → aria-hidden（旁邊已有文字的裝飾圖示）。
 * 本元件不可互動；觸控目標與焦點外框由外層按鈕負責。
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    name: string
    size?: number | string
    filled?: boolean
    label?: string
  }>(),
  { size: 24, filled: false, label: '' },
)

const iconStyle = computed(() => ({
  fontSize: typeof props.size === 'number' ? `${props.size}px` : props.size,
  fontVariationSettings: `'FILL' ${props.filled ? 1 : 0}, 'wght' 400, 'GRAD' 0, 'opsz' 24`,
}))
</script>

<template>
  <span
    class="material-symbols-rounded m3-icon"
    :style="iconStyle"
    :role="label ? 'img' : undefined"
    :aria-label="label || undefined"
    :aria-hidden="label ? undefined : 'true'"
  >{{ name }}</span>
</template>
