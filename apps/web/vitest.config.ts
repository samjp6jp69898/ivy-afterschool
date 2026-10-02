import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config'

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'happy-dom',
      globals: true,
      include: ['src/**/*.spec.ts'],
      // INFRA-012：封鎖未 mock 的網路、隔離 storage、自動 unmount；時區固定台北（跨午夜案例才可重現）
      setupFiles: ['./src/test/setup.ts'],
      env: { TZ: 'Asia/Taipei' },
      clearMocks: true,
      restoreMocks: true,
      maxWorkers: 2,
    },
  }),
)
