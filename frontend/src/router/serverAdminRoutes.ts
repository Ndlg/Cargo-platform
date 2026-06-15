import type { RouteRecordRaw } from 'vue-router'

import ServerAdminLayout from '../layouts/ServerAdminLayout.vue'
import CustomerAccountsView from '../views/server-admin/CustomerAccountsView.vue'
import ServerAdminDashboardView from '../views/server-admin/ServerAdminDashboardView.vue'

export interface ServerAdminNavigationItem {
  label: string
  path: string
}

export const serverAdminNavigationItems: ServerAdminNavigationItem[] = [
  { label: '平台概览', path: '/admin' },
  { label: '客户账号', path: '/admin/accounts' },
]

export const serverAdminRoutes: RouteRecordRaw = {
  path: '/admin',
  component: ServerAdminLayout,
  children: [
    { path: '', component: ServerAdminDashboardView, meta: { title: '平台概览' } },
    { path: 'accounts', component: CustomerAccountsView, meta: { title: '客户账号' } },
    { path: 'workspaces', redirect: '/admin/accounts' },
    { path: 'tenants', redirect: '/admin/accounts' },
    { path: 'users', redirect: '/admin/accounts' },
    { path: ':pathMatch(.*)*', redirect: '/admin' },
  ],
}

export const serverAdminLegacyRedirects: RouteRecordRaw[] = [
  { path: '/dashboard', redirect: '/admin' },
  { path: '/server-admin', redirect: '/admin' },
  { path: '/server-admin/workspaces', redirect: '/admin/accounts' },
  { path: '/server-admin/tenants', redirect: '/admin/accounts' },
  { path: '/server-admin/users', redirect: '/admin/accounts' },
  { path: '/server-admin/:pathMatch(.*)*', redirect: '/admin' },
  { path: '/workspaces', redirect: '/admin/accounts' },
  { path: '/tenants', redirect: '/admin/accounts' },
  { path: '/users', redirect: '/admin/accounts' },
  { path: '/waybill-modes', redirect: '/admin' },
  { path: '/waybill-templates', redirect: '/admin' },
  { path: '/waybill-template-fields', redirect: '/admin' },
]
