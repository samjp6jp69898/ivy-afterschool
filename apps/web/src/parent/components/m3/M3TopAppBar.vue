<script setup lang="ts">
/**
 * M3 Top App Bar（small）：64px、標題靠左單行省略。左側 48px 欄在 showBack 時放返回鍵，
 * 否則放 leading slot（例如 Logo）；右側 actions slot。返回鍵只 emit back，返回行為由
 * 版面決定。捲動超過 0（或外部傳 scrolled）時底色換成 surface-container，不加陰影。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import M3IconButton from './M3IconButton.vue'

const props = withDefaults(defineProps<{ title: string; showBack?: boolean; scrolled?: boolean }>(), {
  showBack: false,
  scrolled: false,
})

const emit = defineEmits<{ back: [] }>()

const windowScrolled = ref(false)
const isScrolled = computed(() => props.scrolled || windowScrolled.value)

function onScroll(): void {
  windowScrolled.value = window.scrollY > 0
}

onMounted(() => {
  onScroll()
  window.addEventListener('scroll', onScroll, { passive: true })
})

onBeforeUnmount(() => {
  window.removeEventListener('scroll', onScroll)
})
</script>

<template>
  <header
    class="m3-top-app-bar"
    :class="{ 'is-scrolled': isScrolled }"
  >
    <div class="m3-top-app-bar__leading">
      <M3IconButton
        v-if="showBack"
        icon="arrow_back"
        label="返回"
        @click="emit('back')"
      />
      <slot
        v-else
        name="leading"
      />
    </div>
    <h1 class="m3-top-app-bar__title m3-title-large">
      {{ title }}
    </h1>
    <div class="m3-top-app-bar__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.m3-top-app-bar {
  position: sticky;
  top: 0;
  z-index: 5;
  display: grid;
  flex: none;
  grid-template-columns: 48px minmax(0, 1fr) auto;
  align-items: center;
  gap: 4px;
  min-height: 64px;
  padding: 0 4px;
  background: var(--m3-surface);
  color: var(--m3-on-surface);
  transition: background-color var(--m3-dur-short-3) var(--m3-easing-standard);
}

.m3-top-app-bar.is-scrolled {
  background: var(--m3-surface-container);
}

.m3-top-app-bar__leading {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
}

.m3-top-app-bar__title {
  margin: 0;
  padding-left: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.m3-top-app-bar__actions {
  display: inline-flex;
  align-items: center;
}

@media (prefers-reduced-motion: reduce) {
  .m3-top-app-bar {
    transition: none;
  }
}
</style>
