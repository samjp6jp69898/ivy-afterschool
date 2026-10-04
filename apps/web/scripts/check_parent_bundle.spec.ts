// @vitest-environment node
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { build } from 'vite'
import { afterAll, describe, expect, it } from 'vitest'
import { parentModuleGraph } from '../build/parentModuleGraph'
import { findViolations, type ChunkGraph } from './check_parent_bundle.mjs'

const HERE = fileURLToPath(new URL('.', import.meta.url))
const FIXTURES = join(HERE, 'fixtures', 'parent-bundle')
const SCRIPT = join(HERE, 'check_parent_bundle.mjs')
const GRAPH_FILE = '.vite/parent-module-graph.json'
// 每個 case 都實際打包（element-plus 約數秒）
const BUILD_TIMEOUT = 60_000

const tmpDirs: string[] = []
afterAll(() => {
  for (const dir of tmpDirs) rmSync(dir, { recursive: true, force: true })
})

const builds = new Map<string, Promise<string>>()

/** 以 Vite JS API 實際建置 fixture，回傳輸出目錄（含 .vite/parent-module-graph.json）。 */
function buildFixture(name: string): Promise<string> {
  let pending = builds.get(name)
  if (!pending) {
    const root = join(FIXTURES, name)
    const outDir = mkdtempSync(join(tmpdir(), `parent-bundle-${name}-`))
    tmpDirs.push(outDir)
    pending = build({
      root,
      configFile: false,
      logLevel: 'silent',
      plugins: [vue(), parentModuleGraph()],
      build: {
        outDir,
        emptyOutDir: true,
        minify: false,
        rollupOptions: {
          input: { main: join(root, 'index.html'), parent: join(root, 'parent/index.html') },
        },
      },
    }).then(() => outDir)
    builds.set(name, pending)
  }
  return pending
}

async function graphOf(name: string): Promise<ChunkGraph> {
  const outDir = await buildFixture(name)
  return JSON.parse(readFileSync(join(outDir, GRAPH_FILE), 'utf8')) as ChunkGraph
}

describe('parent module graph', () => {
  it('clean build passes', async () => {
    const graph = await graphOf('clean')

    expect(findViolations(graph)).toEqual([])
  }, BUILD_TIMEOUT)

  it('shared fixture admin imports element-plus', () => {
    const admin = readFileSync(join(FIXTURES, 'shared-element-plus/src/main.ts'), 'utf8')

    expect(admin).toMatch(/from 'element-plus'|import 'element-plus'/)
  })

  it('flags element-plus in shared chunk', async () => {
    const graph = await graphOf('shared-element-plus')

    const violations = findViolations(graph)
    const elementPlus = violations.filter((v) => v.module.includes('node_modules/element-plus/'))
    expect(elementPlus.length).toBeGreaterThan(0)
    const parentEntry = graph.find((c) => c.facadeModuleId === 'parent/index.html')
    for (const v of elementPlus) {
      // 後台與家長都引入 → 落在共用 chunk，不是 parent entry chunk，也不是任何 entry
      const owner = graph.find((c) => c.fileName === v.chunk)
      expect(owner?.isEntry).toBe(false)
      expect(v.chunk).not.toBe(parentEntry?.fileName)
      expect(v.chain[0]).toBe(parentEntry?.fileName)
      expect(v.chain.at(-1)).toBe(v.chunk)
    }
    expect(violations.some((v) => v.module === 'src/stores/auth.ts')).toBe(true)
  }, BUILD_TIMEOUT)

  it('flags admin view as shared chunk', async () => {
    const graph = await graphOf('admin-view')

    const violations = findViolations(graph)
    expect(violations.map((v) => v.module)).toEqual(['src/views/LoginView.vue'])
    // 後台以 lazy route 載入、parent 靜態引入 → 變成不是 entry 的共用 chunk
    const owner = graph.find((c) => c.fileName === violations[0]?.chunk)
    expect(owner?.isEntry).toBe(false)
  }, BUILD_TIMEOUT)

  it('flags module inlined into entry chunk', async () => {
    const graph = await graphOf('inlined')

    const violations = findViolations(graph)
    expect(violations.map((v) => v.module)).toEqual(['src/stores/only.ts'])
    const entry = graph.find((c) => c.facadeModuleId === 'parent/index.html')
    expect(violations[0]?.chunk).toBe(entry?.fileName)
    expect(violations[0]?.chain).toEqual([entry?.fileName])
  }, BUILD_TIMEOUT)

  it('ignores dynamic imports', async () => {
    const graph = await graphOf('dynamic')

    // element-plus 確實被打包進某個 chunk（動態），但 parent 靜態圖到不了
    expect(graph.some((c) => c.moduleIds.some((m) => m.includes('node_modules/element-plus/')))).toBe(
      true,
    )
    expect(findViolations(graph)).toEqual([])
  }, BUILD_TIMEOUT)

  it('missing parent entry fails', () => {
    const graph: ChunkGraph = [
      {
        fileName: 'assets/main.js',
        isEntry: true,
        facadeModuleId: 'index.html',
        moduleIds: ['src/main.ts'],
        imports: [],
        dynamicImports: [],
      },
    ]

    expect(() => findViolations(graph)).toThrow(/parent\/index\.html/)
  })

  it('reports each module once even through cycles', () => {
    const chunk = (fileName: string, imports: string[], moduleIds: string[], facade: string | null) => ({
      fileName,
      isEntry: facade !== null,
      facadeModuleId: facade,
      moduleIds,
      imports,
      dynamicImports: [],
    })
    const graph: ChunkGraph = [
      chunk('p.js', ['a.js'], ['src/parent/main.ts'], 'parent/index.html'),
      chunk('a.js', ['b.js'], ['src/stores/auth.ts'], null),
      chunk('b.js', ['a.js', 'c.js'], ['src/stores/auth.ts'], null),
      chunk('c.js', [], ['node_modules/.pnpm/element-plus@2.0.0/node_modules/element-plus/es/a.mjs'], null),
    ]

    expect(findViolations(graph).map((v) => [v.module, v.chain])).toEqual([
      ['src/stores/auth.ts', ['p.js', 'a.js']],
      [
        'node_modules/.pnpm/element-plus@2.0.0/node_modules/element-plus/es/a.mjs',
        ['p.js', 'a.js', 'b.js', 'c.js'],
      ],
    ])
  })

  it('does not flag src/parent modules', () => {
    const graph: ChunkGraph = [
      {
        fileName: 'p.js',
        isEntry: true,
        facadeModuleId: 'parent/index.html',
        moduleIds: ['src/parent/views/Home.vue', 'src/shared/http.ts'],
        imports: [],
        dynamicImports: [],
      },
    ]

    expect(findViolations(graph)).toEqual([])
  })
})

describe('parent module graph CLI', () => {
  const run = (graphPath: string) => {
    try {
      const stdout = execFileSync('node', [SCRIPT, '--graph', graphPath], { encoding: 'utf8' })
      return { code: 0, stdout, stderr: '' }
    } catch (error) {
      const e = error as { status: number; stdout: string; stderr: string }
      return { code: e.status, stdout: e.stdout, stderr: e.stderr }
    }
  }

  it('exits 0 and prints counts for clean build', async () => {
    const outDir = await buildFixture('clean')

    const result = run(join(outDir, GRAPH_FILE))

    expect(result.code).toBe(0)
    expect(result.stdout).toMatch(/\d+ 個 chunk、\d+ 個模組/)
  }, BUILD_TIMEOUT)

  it('exits 1 and names the module for element-plus build', async () => {
    const outDir = await buildFixture('shared-element-plus')

    const result = run(join(outDir, GRAPH_FILE))

    expect(result.code).toBe(1)
    expect(result.stderr).toMatch(/家長端 entry 靜態引入了 .*node_modules\/.*element-plus\/.*（位於 chunk assets\//)
  }, BUILD_TIMEOUT)

  it('exits 1 when graph file is missing', () => {
    const result = run(resolve(tmpdir(), 'parent-bundle-not-exist.json'))

    expect(result.code).toBe(1)
    expect(result.stderr).toContain('vite build')
  })
})
