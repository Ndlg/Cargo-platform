import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import LoginView from '../views/LoginView.vue'
import { useSessionStore } from '../stores/session'
import { clientAdminRoutes } from './clientAdminRoutes'
import { clientRoutes } from './clientRoutes'
import { tenantLegacyRedirects } from './tenantRedirects'

const routes: RouteRecordRaw[] = [
  { path: '/login', component: LoginView, meta: { public: true, defaultRedirect: '/' } },
  clientRoutes,
  clientAdminRoutes,
  ...tenantLegacyRedirects,
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const session = useSessionStore()

  if (to.meta.public) {
    if (to.path === '/login' && session.isAuthenticated) {
      try {
        const ready = await session.ensureSession()
        if (ready) return '/'
      } catch {
        session.clearSession()
      }
    }
    return true
  }

  if (!session.isAuthenticated) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  try {
    const ready = await session.ensureSession()
    if (!ready) return { path: '/login', query: { redirect: to.fullPath } }
  } catch {
    session.clearSession()
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  return true
})

export default router
