<script setup lang="ts">
// FRONTEND-024：尚未實作的 view 佔位。各 view task 完成時把 viewLoaders.ts 中對應的 loader 換成真正的 view。
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <div class="placeholder-view">
    此頁面尚未實作：{{ route.meta.title }}
  </div>
</template>

<style scoped>
.placeholder-view {
  padding: 24px;
  color: var(--el-text-color-secondary);
}
</style>
