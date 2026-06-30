import type { RouteRecordRaw } from 'vue-router'

import ClientLayout from '../layouts/ClientLayout.vue'
import CaptureRecordsView from '../views/workbench/CaptureRecordsView.vue'
import ClientHomeView from '../views/client/ClientHomeView.vue'
import ExceptionsView from '../views/workbench/ExceptionsView.vue'
import ExportCenterView from '../views/workbench/ExportCenterView.vue'
import WaybillBatchesView from '../views/workbench/WaybillBatchesView.vue'

export const clientRoutes: RouteRecordRaw = {
  path: '/',
  component: ClientLayout,
  children: [
    { path: '', component: ClientHomeView, meta: { title: '业务页面' } },
    {
      path: 'capture-records',
      component: CaptureRecordsView,
      meta: { title: '采集记录' },
    },
    {
      path: 'waybill-batches',
      component: WaybillBatchesView,
      meta: { title: '面单解析' },
    },
    {
      path: 'order-rows',
      redirect: '/waybill-batches',
    },
    {
      path: 'product-matching',
      redirect: '/exceptions',
    },
    { path: 'exceptions', component: ExceptionsView, meta: { title: '异常处理' } },
    { path: 'exports', component: ExportCenterView, meta: { title: '导出中心' } },
  ],
}
