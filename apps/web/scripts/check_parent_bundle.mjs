// 家長端 bundle 檢查（INFRA-029 / INFRA-050）：parent/index.html 靜態 import 到的 chunk，
// 其實際包含的模組不得含 Element Plus 或後台程式碼。
// 資料來源是 build/parentModuleGraph.ts 輸出的 .vite/parent-module-graph.json（模組層級；manifest 看不到共用 chunk 的內容）。
// 用法：node scripts/check_parent_bundle.mjs [--graph <path>]（預設 dist/.vite/parent-module-graph.json）
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const PARENT_ENTRY = 'parent/index.html'
// 整包 element-plus 被引入時有數百個模組，只列前幾個
const MAX_REPORTED = 20

// pnpm 的 node_modules/.pnpm/<pkg>@<ver>/node_modules/element-plus/ 也含 /node_modules/element-plus/
const FORBIDDEN_MODULE = [
  /(?:^|\/)node_modules\/@?element-plus\//,
  /^src\/(?:views|components|layouts|stores)\//,
]

function isForbidden(moduleId) {
  return FORBIDDEN_MODULE.some((re) => re.test(moduleId))
}

/**
 * 從 parent entry chunk 沿 imports（不含 dynamicImports）廣度優先走訪，entry chunk 本身也檢查。
 * 每個違規模組只報一次（取最短 chunk 鏈）。
 * @typedef {{fileName: string, isEntry: boolean, facadeModuleId: string | null, moduleIds: string[], imports: string[], dynamicImports: string[]}} Chunk
 * @param {Chunk[]} graph
 * @returns {{module: string, chunk: string, chain: string[]}[]}
 */
export function findViolations(graph) {
  const byName = new Map(graph.map((chunk) => [chunk.fileName, chunk]))
  const entry = graph.find((chunk) => chunk.facadeModuleId === PARENT_ENTRY)
  if (!entry) throw new Error(`graph 找不到家長端 entry chunk（facadeModuleId = ${PARENT_ENTRY}）`)

  const violations = []
  const reported = new Set()
  const parent = new Map([[entry.fileName, null]])
  const queue = [entry.fileName]
  while (queue.length > 0) {
    const fileName = queue.shift()
    const chunk = byName.get(fileName)
    if (!chunk) continue
    for (const moduleId of chunk.moduleIds) {
      if (!isForbidden(moduleId) || reported.has(moduleId)) continue
      reported.add(moduleId)
      const chain = []
      for (let k = fileName; k !== null; k = parent.get(k)) chain.unshift(k)
      violations.push({ module: moduleId, chunk: fileName, chain })
    }
    for (const next of chunk.imports) {
      if (parent.has(next)) continue
      parent.set(next, fileName)
      queue.push(next)
    }
  }
  return violations
}

/** 靜態走訪到的 chunk 數與模組數（模組去重），供成功訊息使用。 */
function countReachable(graph) {
  const byName = new Map(graph.map((chunk) => [chunk.fileName, chunk]))
  const entry = graph.find((chunk) => chunk.facadeModuleId === PARENT_ENTRY)
  const seen = new Set([entry.fileName])
  const modules = new Set()
  const queue = [entry.fileName]
  while (queue.length > 0) {
    const chunk = byName.get(queue.shift())
    if (!chunk) continue
    for (const id of chunk.moduleIds) modules.add(id)
    for (const next of chunk.imports) {
      if (!seen.has(next)) {
        seen.add(next)
        queue.push(next)
      }
    }
  }
  return { chunks: seen.size, modules: modules.size }
}

function main(argv) {
  const flag = argv.indexOf('--graph')
  const graphPath =
    flag >= 0 && argv[flag + 1]
      ? resolve(argv[flag + 1])
      : resolve(dirname(fileURLToPath(import.meta.url)), '../dist/.vite/parent-module-graph.json')
  let graph
  try {
    graph = JSON.parse(readFileSync(graphPath, 'utf8'))
  } catch (error) {
    console.error(`讀不到 ${graphPath}：${error.message}（先執行 vite build）`)
    return 1
  }
  let violations
  try {
    violations = findViolations(graph)
  } catch (error) {
    console.error(error.message)
    return 1
  }
  for (const { module, chunk, chain } of violations.slice(0, MAX_REPORTED)) {
    console.error(
      `家長端 entry 靜態引入了 ${module}（位於 chunk ${chunk}，經由 ${chain.join(' -> ')}）`,
    )
  }
  if (violations.length > MAX_REPORTED) {
    console.error(`…另有 ${violations.length - MAX_REPORTED} 個違規模組未列出（共 ${violations.length} 個）`)
  }
  if (violations.length > 0) return 1
  const { chunks, modules } = countReachable(graph)
  console.log(`家長端 bundle 檢查通過：靜態收集 ${chunks} 個 chunk、${modules} 個模組`)
  return 0
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main(process.argv.slice(2))
}
