// 後台入口最小佔位，由 FRONTEND 的 app 啟動 task 取代
import { createApp } from 'vue'

createApp({ render: () => null }).mount('#app')
