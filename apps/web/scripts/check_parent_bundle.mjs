// 家長端 bundle 檢查（INFRA-029）：parent/index.html 靜態 import 的 chunk 不得含 Element Plus 或後台程式碼。
// 用法：node scripts/check_parent_bundle.mjs [--manifest <path>]（預設 dist/.vite/manifest.json）
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const PARENT_ENTRY = 'parent/index.html'

const FORBIDDEN = [
  /node_modules\/@?element-plus\//,
  /(?:^|\/)src\/(?:views|components|layouts|stores)\//,
]
// chunk 名稱沒有路徑時（共用 chunk 以第一個模組命名）
const FORBIDDEN_NAME = /^@?element-plus(?:\/|$|-)/

function isForbidden(key, chunk) {
  const paths = [key, chunk.src].filter((v) => typeof v === 'string')
  if (paths.some((p) => FORBIDDEN.some((re) => re.test(p)))) return true
  return typeof chunk.name === 'string' && FORBIDDEN_NAME.test(chunk.name)
}

/**
 * 從 parent entry 沿 imports（不含 dynamicImports）廣度優先走訪，回傳違規 chunk 與最短路徑鏈。
 * @param {Record<string, {src?: string, name?: string, imports?: string[]}>} manifest
 * @returns {{chunk: string, chain: string[]}[]}
 */
export function findViolations(manifest) {
  if (!Object.hasOwn(manifest, PARENT_ENTRY)) {
    throw new Error(`manifest 找不到家長端 entry ${PARENT_ENTRY}`)
  }
  const violations = []
  const parent = new Map([[PARENT_ENTRY, null]])
  const queue = [PARENT_ENTRY]
  while (queue.length > 0) {
    const key = queue.shift()
    const chunk = manifest[key] ?? {}
    if (key !== PARENT_ENTRY && isForbidden(key, chunk)) {
      const chain = []
      for (let k = key; k !== null; k = parent.get(k)) chain.unshift(k)
      violations.push({ chunk: key, chain })
    }
    for (const next of chunk.imports ?? []) {
      if (parent.has(next)) continue
      parent.set(next, key)
      queue.push(next)
    }
  }
  return violations
}

/** 走訪到的 chunk 數（含 entry），供成功訊息使用。 */
function countReachable(manifest) {
  const seen = new Set([PARENT_ENTRY])
  const queue = [PARENT_ENTRY]
  while (queue.length > 0) {
    for (const next of manifest[queue.shift()]?.imports ?? []) {
      if (!seen.has(next)) {
        seen.add(next)
        queue.push(next)
      }
    }
  }
  return seen.size
}

function main(argv) {
  const flag = argv.indexOf('--manifest')
  const manifestPath =
    flag >= 0 && argv[flag + 1]
      ? resolve(argv[flag + 1])
      : resolve(dirname(fileURLToPath(import.meta.url)), '../dist/.vite/manifest.json')
  let manifest
  try {
    manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  } catch (error) {
    console.error(`讀不到 manifest ${manifestPath}：${error.message}（先執行 vite build）`)
    return 1
  }
  let violations
  try {
    violations = findViolations(manifest)
  } catch (error) {
    console.error(error.message)
    return 1
  }
  for (const { chunk, chain } of violations) {
    console.error(`家長端 entry 靜態引入了 ${chunk}（經由 ${chain.join(' -> ')}）`)
  }
  if (violations.length > 0) return 1
  console.log(`家長端 bundle 檢查通過：靜態收集 ${countReachable(manifest)} 個 chunk`)
  return 0
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main(process.argv.slice(2))
}
