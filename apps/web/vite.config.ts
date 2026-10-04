import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import Components from 'unplugin-vue-components/vite'
import { defineConfig } from 'vite'
import { parentModuleGraph } from './build/parentModuleGraph.ts'

// 單一 Vite 專案、兩個 HTML 入口：index.html（後台）與 parent/index.html（家長端）
export default defineConfig({
  plugins: [
    vue(),
    // 輸出 chunk 模組圖，供家長端 bundle 檢查（INFRA-050）
    parentModuleGraph(),
    AutoImport({
      resolvers: [ElementPlusResolver()],
      dts: 'src/auto-imports.d.ts',
    }),
    Components({
      resolvers: [ElementPlusResolver()],
      // 專案自己的元件一律顯式 import，避免家長端被自動帶入後台元件
      dirs: [],
      dts: 'src/components.d.ts',
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        parent: fileURLToPath(new URL('./parent/index.html', import.meta.url)),
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5341,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8341',
        ws: true,
        changeOrigin: false,
      },
    },
  },
})
