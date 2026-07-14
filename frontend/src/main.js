/**
 * Vue 应用入口文件
 *
 * 创建并挂载 Vue 应用
 */

import {createApp} from 'vue'
import App from './App.vue'
import './styles/main.css'

// 创建 Vue 应用实例
const app = createApp(App)

// 挂载到 #app 元素
app.mount('#app')
