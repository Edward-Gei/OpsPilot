// 应用入口：装配 Pinia / Router / Ant Design Vue
import { createPinia } from 'pinia'
import { createApp } from 'vue'
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import './styles/tokens.css'
import './styles/auth-theme.css'

import App from './App.vue'
import router from './router'

const app = createApp(App)

// 全局错误处理：捕获未被组件 onErrorCaptured 拦截的错误，防止渲染树中断白屏且无迹可寻
app.config.errorHandler = (err, _instance, info) => {
  console.error('[GlobalError]', err, info)
}
// 全局警告处理：收敛 Vue 运行时警告，便于开发期排查
app.config.warnHandler = (msg, _instance, trace) => {
  console.warn('[VueWarn]', msg, trace)
}

app.use(createPinia())
app.use(router)
app.use(Antd)
app.mount('#app')
