import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import LoginView from '../views/LoginView.vue'
import HostView from '../views/HostView.vue'
import ViewerView from '../views/ViewerView.vue'

const routes = [
  { path: '/', redirect: '/host' },
  { path: '/login', name: 'login', component: LoginView },
  {
    path: '/host',
    name: 'host',
    component: HostView,
    meta: { requiresHost: true },
  },
  { path: '/viewer', name: 'viewer', component: ViewerView },
  { path: '/:pathMatch(.*)*', redirect: '/host' },
]

const router = createRouter({
  // Hash 模式：静态托管到 /app/ 时无需服务端做 SPA fallback。
  history: createWebHashHistory(),
  routes,
})

router.beforeEach(async (to) => {
  if (!to.meta.requiresHost) return true
  const auth = useAuthStore()
  const ok = await auth.ensureSession()
  if (!ok) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  return true
})

export default router
