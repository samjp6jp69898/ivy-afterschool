import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config.ts'

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'happy-dom',
      globals: true,
      // INFRA-029：scripts/ 下的建置檢查腳本也有 spec
      include: ['src/**/*.spec.ts', 'scripts/**/*.spec.ts'],
      // INFRA-012：封鎖未 mock 的網路、隔離 storage、自動 unmount；時區固定台北（跨午夜案例才可重現）
      setupFiles: ['./src/test/setup.ts'],
      env: { TZ: 'Asia/Taipei' },
      clearMocks: true,
      restoreMocks: true,
      maxWorkers: 2,
      // INFRA-043：ElementPlusResolver 會自動 import theme-chalk CSS；element-plus 交給 vite 轉譯，
      // 否則 Node 直接載入 .css 會拋 Unknown file extension ".css"
      server: { deps: { inline: ['element-plus'] } },
    },
  }),
)
