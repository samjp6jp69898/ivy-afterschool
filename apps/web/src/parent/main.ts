// 家長端入口最小佔位，由 PARENT 的 app 啟動 task 取代
import { createApp } from 'vue'

createApp({ render: () => null }).mount('#app')
