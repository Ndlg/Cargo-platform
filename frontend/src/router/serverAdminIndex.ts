import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import LoginView from '../views/LoginView.vue'
import { useSessionStore } from '../stores/session'
import { serverAdminLegacyRedirects, serverAdminRoutes } from './serverAdminRoutes'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/admin' },
  {
    path: '/login',
    component: LoginView,
    meta: { public: true, defaultRedirect: '/admin' },
  },
  serverAdminRoutes,
  ...serverAdminLegacyRedirects,
  { path: '/:pathMatch(.*)*', redirect: '/admin' },
]

const serverAdminRouter = createRouter({
  history: createWebHistory(),
  routes,
})

serverAdminRouter.beforeEach(async (to) => {
  const session = useSessionStore()

  if (to.meta.public) {
    if (to.path === '/login' && session.isAuthenticated) {
      try {
        const ready = await session.ensureSession()
        if (ready) return '/admin'
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

export default serverAdminRouter
