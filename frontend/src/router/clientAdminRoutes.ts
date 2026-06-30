import type { RouteRecordRaw } from 'vue-router'

import ClientAdminLayout from '../layouts/ClientAdminLayout.vue'
import ClientAdminHomeView from '../views/client-admin/ClientAdminHomeView.vue'
import CollectorConnectionsView from '../views/workbench/CollectorConnectionsView.vue'
import ExportHeaderDefinitionView from '../views/workbench/ExportHeaderDefinitionView.vue'
import ProductCatalogView from '../views/workbench/ProductCatalogView.vue'
import ProductMatchingView from '../views/workbench/ProductMatchingView.vue'
import RecognitionRulePacksView from '../views/workbench/RecognitionRulePacksView.vue'
import StallCatalogView from '../views/workbench/StallCatalogView.vue'
import SystemSettingsView from '../views/workbench/SystemSettingsView.vue'

export const clientAdminRoutes: RouteRecordRaw = {
  path: '/admin',
  component: ClientAdminLayout,
  children: [
    { path: '', component: ClientAdminHomeView, meta: { title: '管理页面' } },
    {
      path: 'collector-connections',
      component: CollectorConnectionsView,
      meta: { title: '采集连接' },
    },
    { path: 'export-headers', component: ExportHeaderDefinitionView, meta: { title: '导出表头' } },
    { path: 'stalls', component: StallCatalogView, meta: { title: '档口库' } },
    { path: 'products', component: ProductCatalogView, meta: { title: '商品/SKU' } },
    { path: 'product-matching', component: ProductMatchingView, meta: { title: '商品匹配' } },
    { path: 'recognition-rule-packs', component: RecognitionRulePacksView, meta: { title: '识别规则包' } },
    { path: 'system-settings', component: SystemSettingsView, meta: { title: '系统设置' } },
  ],
}
