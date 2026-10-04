// ESLint flat config。只透過 just web-lint <file...> 執行，禁止無參數全量 lint。
import { defineConfig } from 'eslint/config'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'
import tseslint from 'typescript-eslint'

// 允許附說明的 @ts-expect-error；禁止 @ts-ignore / @ts-nocheck（不會隨型別修好而報錯）
const banTsComment = [
  'error',
  {
    'ts-expect-error': 'allow-with-description',
    'ts-ignore': true,
    'ts-nocheck': true,
    'ts-check': false,
    minimumDescriptionLength: 3,
  },
]

// toISOString() 是 UTC，台北（UTC+8）凌晨 0~8 點取日期會變成前一天
const UTC_DATE_SLICE_MESSAGE =
  'toISOString() 是 UTC，台北會跨日；取 YYYY-MM-DD 請用 src/shared/ 的台北日期工具'
const noUtcDateSlice = [
  'error',
  {
    selector:
      "CallExpression[callee.property.name='slice'][callee.object.callee.property.name='toISOString']",
    message: UTC_DATE_SLICE_MESSAGE,
  },
  {
    selector:
      "CallExpression[callee.property.name='split'][callee.object.callee.property.name='toISOString']",
    message: UTC_DATE_SLICE_MESSAGE,
  },
]

// 家長端不得依賴後台（Element Plus 與後台的 views / components / stores / layouts）
const PARENT_FORBIDDEN_IMPORTS = [
  'element-plus',
  'element-plus/*',
  '@element-plus/*',
  '@/views',
  '@/views/*',
  '@/components',
  '@/components/*',
  '@/stores',
  '@/stores/*',
  '@/layouts',
  '@/layouts/*',
]

export default defineConfig(
  {
    ignores: [
      'dist/**',
      'coverage/**',
      'playwright-report/**',
      'test-results/**',
      'scripts/fixtures/**',
      'src/auto-imports.d.ts',
      'src/components.d.ts',
    ],
  },
  {
    linterOptions: { reportUnusedDisableDirectives: 'error' },
  },
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.vue'],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
        ecmaVersion: 'latest',
        sourceType: 'module',
      },
    },
  },
  {
    files: ['**/*.{ts,vue}'],
    languageOptions: {
      globals: { ...globals.browser },
    },
    rules: {
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/ban-ts-comment': banTsComment,
      'no-restricted-syntax': noUtcDateSlice,
    },
  },
  {
    files: ['*.config.{ts,js}'],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
  {
    files: ['src/parent/**/*.{ts,vue}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: PARENT_FORBIDDEN_IMPORTS,
              message: '家長端不可依賴 Element Plus 或後台程式碼；共用程式放 src/shared/',
            },
          ],
        },
      ],
    },
  },
  {
    files: ['src/**/*.{ts,vue}'],
    ignores: ['src/parent/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@/parent', '@/parent/*'],
              message: '後台不可 import 家長端程式碼；共用程式放 src/shared/',
            },
          ],
        },
      ],
    },
  },
)
