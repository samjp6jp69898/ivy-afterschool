// INFRA-014：兩個入口的 app shell 都有在服務（執行前先 just web 或 just up）
import { expect, test } from '@playwright/test'

test.describe('smoke', () => {
  test('smoke admin entry serves app shell', async ({ page }) => {
    const response = await page.goto('/')

    expect(response?.status()).toBe(200)
    await expect(page.locator('#app')).toHaveCount(1)
  })

  test('smoke parent entry serves app shell', async ({ page }) => {
    const response = await page.goto('/parent/')

    expect(response?.status()).toBe(200)
    await expect(page.locator('#app')).toHaveCount(1)
  })
})
