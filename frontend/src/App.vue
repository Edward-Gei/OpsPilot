<script setup lang="ts">
// 根组件：中文语言包 + 双主题（antd 算法随 theme store 联动切换）
import { computed } from 'vue'
import { theme as antdTheme } from 'ant-design-vue'
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import { useThemeStore } from '@/stores/theme'

const themeStore = useThemeStore()

// antd 主题映射：算法（暗/亮）+ 与 tokens.css 一致的品牌令牌
const antdThemeConfig = computed(() => ({
  algorithm:
    themeStore.mode === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
  token: {
    colorPrimary: themeStore.mode === 'dark' ? '#3b82f6' : '#2563eb',
    borderRadius: 10,
    ...(themeStore.mode === 'dark'
      ? { colorBgContainer: '#111a2e', colorBgElevated: '#16213a' }
      : {}),
  },
}))
</script>

<template>
  <a-config-provider :locale="zhCN" :theme="antdThemeConfig">
    <router-view />
  </a-config-provider>
</template>

<style>
html,
body,
#app {
  height: 100%;
  margin: 0;
}
</style>
