/// <reference types="vite/client" />

// Vue SFC 模块声明，保证 TS 能识别 .vue 导入
declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<object, object, unknown>
  export default component
}
