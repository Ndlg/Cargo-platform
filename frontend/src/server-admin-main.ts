import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import 'dayjs/locale/zh-cn'

import App from './App.vue'
import serverAdminRouter from './router/serverAdminIndex'
import './styles/base.css'

createApp(App).use(createPinia()).use(serverAdminRouter).use(ElementPlus, { locale: zhCn }).mount('#app')
