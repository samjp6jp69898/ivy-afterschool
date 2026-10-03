<!-- INFRA-043：測試用元件。<el-*> 不手動 import，由 vite.config.ts 的 ElementPlusResolver 自動 import（含 theme-chalk CSS） -->
<script setup lang="ts">
import { Search } from '@element-plus/icons-vue'
</script>

<template>
  <el-button type="primary">
    送出
  </el-button>
  <el-icon>
    <Search />
  </el-icon>
</template>
