import type { RouteRecordRaw } from 'vue-router'

import ClientAdminLayout from '../layouts/ClientAdminLayout.vue'
import ClientAdminHomeView from '../views/client-admin/ClientAdminHomeView.vue'
import CollectorConnectionsView from '../views/workbench/CollectorConnectionsView.vue'
import ExportHeaderDefinitionView from '../views/workbench/ExportHeaderDefinitionView.vue'
import MatchingReviewView from '../views/workbench/MatchingReviewView.vue'
import PrintTemplateRulesView from '../views/workbench/PrintTemplateRulesView.vue'
import ProductCatalogView from '../views/workbench/ProductCatalogView.vue'
import StallCatalogView from '../views/workbench/StallCatalogView.vue'

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
    { path: 'field-definition', redirect: '/admin/export-headers' },
    { path: 'print-template-rules', component: PrintTemplateRulesView, meta: { title: '打印模板规则' } },
    { path: 'export-headers', component: ExportHeaderDefinitionView, meta: { title: '导出表头' } },
    { path: 'key-fields', redirect: '/admin/matching' },
    { path: 'stalls', component: StallCatalogView, meta: { title: '档口库' } },
    { path: 'products', component: ProductCatalogView, meta: { title: '商品/SKU' } },
    { path: 'matching', component: MatchingReviewView, meta: { title: '商品识别' } },
    { path: 'match-rules', redirect: '/admin/matching' },
  ],
}
