// 主题状态：暗色/浅色双主题切换，偏好持久化到 localStorage
import { defineStore } from 'pinia'
import { ref } from 'vue'

export type ThemeMode = 'dark' | 'light'

const STORAGE_KEY = 'opspilot:theme'

/** 将主题应用到 html[data-theme]，驱动 tokens.css 中的 CSS 变量切换 */
function applyTheme(mode: ThemeMode) {
  document.documentElement.dataset.theme = mode
}

export const useThemeStore = defineStore('theme', () => {
  // 默认暗色；读取历史偏好（仅接受合法值，防脏数据）
  const saved = localStorage.getItem(STORAGE_KEY)
  const mode = ref<ThemeMode>(saved === 'light' ? 'light' : 'dark')
  applyTheme(mode.value)

  // 切换主题并持久化
  function toggle() {
    mode.value = mode.value === 'dark' ? 'light' : 'dark'
    localStorage.setItem(STORAGE_KEY, mode.value)
    applyTheme(mode.value)
  }

  return { mode, toggle }
})
