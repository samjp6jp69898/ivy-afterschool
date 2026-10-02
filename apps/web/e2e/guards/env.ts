// INFRA-014：e2e 環境守衛。e2e 只允許打本機服務（docs/testing_conventions.md §3），
// playwright.config.ts 載入時即呼叫，非本機連測試清單都列不出來（fail-closed）。
export const DEFAULT_BASE_URL = 'http://127.0.0.1:5341'

const LOCAL_HOSTNAMES = new Set(['127.0.0.1', 'localhost'])

/** 讀 E2E_BASE_URL（預設本機 Vite dev server）；hostname 不是本機時 throw。 */
export function resolveBaseUrl(env: NodeJS.ProcessEnv): string {
  const raw = env.E2E_BASE_URL?.trim() || DEFAULT_BASE_URL
  let hostname: string
  try {
    hostname = new URL(raw).hostname
  } catch {
    throw new Error(`E2E_BASE_URL 不是合法的 URL：${raw}`)
  }
  if (!LOCAL_HOSTNAMES.has(hostname)) {
    throw new Error(`E2E 只允許本機：${hostname}`)
  }
  return raw
}
