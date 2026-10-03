// INFRA-043：Element Plus 元件經 unplugin-vue-components 自動 import（含 theme-chalk CSS）時可在 vitest 中載入
// 自動 import 只作用在經 vite 編譯的 SFC（.ts 內的 template 字串不會被 unplugin 轉換），所以用 .vue 測試元件
import { mount } from '@vue/test-utils'
import vitestConfigSource from '../../vitest.config.ts?raw'
import ElementPlusProbe from './fixtures/ElementPlusProbe.vue'

describe('elementPlus', () => {
  it('elementPlus auto-imported components render in tests', () => {
    const wrapper = mount(ElementPlusProbe)

    expect(wrapper.find('.el-button').exists()).toBe(true)
    expect(wrapper.find('.el-button').text()).toContain('送出')
    expect(wrapper.find('.el-icon svg').exists()).toBe(true)
  })

  it('elementPlus vitest config inlines element-plus and imports vite config with extension', () => {
    expect(vitestConfigSource).toMatch(/inline:\s*\[[^\]]*'element-plus'[^\]]*\]/)
    expect(vitestConfigSource).not.toMatch(/from\s+'\.\/vite\.config'/)
    expect(vitestConfigSource).toMatch(/from\s+'\.\/vite\.config\.ts'/)
  })
})
