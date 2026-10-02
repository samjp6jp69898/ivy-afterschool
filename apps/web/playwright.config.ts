// INFRA-014：Playwright e2e 設定。只跑本機全套（服務由 just up 啟動，這裡不設 webServer），
// 預設不執行，透過 just e2e <spec> 指定檔案執行。首次使用先 pnpm exec playwright install chromium。
import { defineConfig, devices } from '@playwright/test'

import { resolveBaseUrl } from './e2e/guards/env'

// 載入即驗證：E2E_BASE_URL 不是本機時這裡直接 throw，連測試清單都列不出來
const baseURL = resolveBaseUrl(process.env)

export default defineConfig({
  testDir: 'e2e',
  outputDir: 'test-results',
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    trace: 'on-first-retry',
  },
  projects: [
    {
      // 後台：桌面 Chrome
      name: 'admin-chromium',
      testMatch: ['e2e/admin/**', 'e2e/smoke.spec.ts'],
      use: { ...devices['Desktop Chrome'] },
    },
    {
      // 家長端（LIFF）：手機尺寸
      name: 'parent-mobile',
      testMatch: ['e2e/parent/**'],
      use: { ...devices['Pixel 7'] },
    },
  ],
})
