// 輸出每個 chunk 實際包含的模組（INFRA-050），供 scripts/check_parent_bundle.mjs 檢查家長端 bundle。
// manifest 只有 entry 與 dynamic entry 帶 src，靜態共用 chunk 與併進 entry chunk 的模組在 manifest 裡看不到。
import { relative, sep } from 'node:path'
import type { Plugin } from 'vite'

export const PARENT_MODULE_GRAPH_FILE = '.vite/parent-module-graph.json'

interface ChunkRecord {
  fileName: string
  isEntry: boolean
  facadeModuleId: string | null
  moduleIds: string[]
  imports: string[]
  dynamicImports: string[]
}

export function parentModuleGraph(): Plugin {
  let root = process.cwd()

  // 相對 Vite root 的 POSIX 路徑；去掉 ?vue&type=... 之類的 query；虛擬模組（\0 開頭）回 null
  const toRelative = (id: string): string | null => {
    if (id.startsWith('\0')) return null
    const path = id.split('?')[0] ?? id
    return relative(root, path).split(sep).join('/')
  }

  return {
    name: 'afterschool:parent-module-graph',
    apply: 'build',
    enforce: 'post',
    configResolved(config) {
      root = config.root
    },
    generateBundle(_options, bundle) {
      const chunks: ChunkRecord[] = []
      for (const item of Object.values(bundle)) {
        if (item.type !== 'chunk') continue
        const moduleIds = new Set<string>()
        for (const id of item.moduleIds) {
          const rel = toRelative(id)
          if (rel !== null) moduleIds.add(rel)
        }
        chunks.push({
          fileName: item.fileName,
          isEntry: item.isEntry,
          facadeModuleId: item.facadeModuleId === null ? null : toRelative(item.facadeModuleId),
          moduleIds: [...moduleIds],
          imports: item.imports,
          dynamicImports: item.dynamicImports,
        })
      }
      this.emitFile({
        type: 'asset',
        fileName: PARENT_MODULE_GRAPH_FILE,
        source: JSON.stringify(chunks, null, 2),
      })
    },
  }
}
