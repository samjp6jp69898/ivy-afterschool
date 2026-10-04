import { describe, expect, it } from 'vitest'
import { findViolations } from './check_parent_bundle.mjs'

const ENTRY = 'parent/index.html'

describe('parent bundle check', () => {
  it('passes clean manifest', () => {
    const manifest = {
      [ENTRY]: {
        file: 'assets/parent-a1.js',
        isEntry: true,
        imports: ['src/shared/http.ts', '_vue-b2.js'],
      },
      'src/shared/http.ts': { file: 'assets/http-c3.js', src: 'src/shared/http.ts' },
      '_vue-b2.js': { file: 'assets/vue-b2.js', name: 'vue', imports: [] },
      'node_modules/vue/dist/vue.runtime.esm-bundler.js': { file: 'assets/vue-d4.js' },
    }

    expect(findViolations(manifest)).toEqual([])
  })

  it('flags element-plus reached through an intermediate chunk', () => {
    const manifest = {
      [ENTRY]: { file: 'assets/parent-a1.js', isEntry: true, imports: ['A'] },
      A: { file: 'assets/a.js', imports: ['node_modules/element-plus/es/index.mjs'] },
      'node_modules/element-plus/es/index.mjs': {
        file: 'assets/el.js',
        src: 'node_modules/element-plus/es/index.mjs',
      },
    }

    expect(findViolations(manifest)).toEqual([
      {
        chunk: 'node_modules/element-plus/es/index.mjs',
        chain: [ENTRY, 'A', 'node_modules/element-plus/es/index.mjs'],
      },
    ])
  })

  it('flags @element-plus scoped packages', () => {
    const manifest = {
      [ENTRY]: { file: 'assets/p.js', isEntry: true, imports: ['icons'] },
      icons: { file: 'assets/i.js', src: 'node_modules/@element-plus/icons-vue/dist/index.js' },
    }

    expect(findViolations(manifest).map((v) => v.chunk)).toEqual(['icons'])
  })

  it('flags admin views', () => {
    const manifest = {
      [ENTRY]: { file: 'assets/p.js', isEntry: true, imports: ['src/views/LoginView.vue'] },
      'src/views/LoginView.vue': { file: 'assets/login.js', src: 'src/views/LoginView.vue' },
    }

    expect(findViolations(manifest)).toEqual([
      { chunk: 'src/views/LoginView.vue', chain: [ENTRY, 'src/views/LoginView.vue'] },
    ])
  })

  it.each(['src/components/X.vue', 'src/layouts/Main.vue', 'src/stores/auth.ts'])(
    'flags admin directory %s',
    (src) => {
      const manifest = {
        [ENTRY]: { file: 'assets/p.js', isEntry: true, imports: ['chunk'] },
        chunk: { file: 'assets/c.js', src },
      }

      expect(findViolations(manifest).map((v) => v.chunk)).toEqual(['chunk'])
    },
  )

  it('does not flag parent own code under src/parent', () => {
    const manifest = {
      [ENTRY]: { file: 'assets/p.js', isEntry: true, imports: ['src/parent/views/Home.vue'] },
      'src/parent/views/Home.vue': { file: 'assets/h.js', src: 'src/parent/views/Home.vue' },
    }

    expect(findViolations(manifest)).toEqual([])
  })

  it('ignores dynamic imports', () => {
    const manifest = {
      [ENTRY]: {
        file: 'assets/p.js',
        isEntry: true,
        imports: [],
        dynamicImports: ['node_modules/element-plus/es/index.mjs'],
      },
      'node_modules/element-plus/es/index.mjs': {
        file: 'assets/el.js',
        src: 'node_modules/element-plus/es/index.mjs',
      },
    }

    expect(findViolations(manifest)).toEqual([])
  })

  it('handles cycles', () => {
    const manifest = {
      [ENTRY]: { file: 'assets/p.js', isEntry: true, imports: ['A'] },
      A: { file: 'assets/a.js', imports: ['B'] },
      B: { file: 'assets/b.js', imports: ['A'] },
    }

    expect(findViolations(manifest)).toEqual([])
  })

  it('reports a violation reachable only through a cycle exactly once', () => {
    const manifest = {
      [ENTRY]: { file: 'assets/p.js', isEntry: true, imports: ['A'] },
      A: { file: 'assets/a.js', imports: ['B'] },
      B: { file: 'assets/b.js', imports: ['A', 'src/stores/auth.ts'] },
      'src/stores/auth.ts': { file: 'assets/auth.js', src: 'src/stores/auth.ts' },
    }

    expect(findViolations(manifest)).toEqual([
      { chunk: 'src/stores/auth.ts', chain: [ENTRY, 'A', 'B', 'src/stores/auth.ts'] },
    ])
  })

  it('throws when the parent entry is missing from the manifest', () => {
    expect(() => findViolations({ 'index.html': { file: 'assets/m.js' } })).toThrow(
      /parent\/index\.html/,
    )
  })
})
