from fastapi import APIRouter

from app.api.routes import (
    auth,
    collector_runtime,
    export_fields,
    health,
    order_row_drafts,
    platform_accounts,
    product_assets,
    product_sku_linking,
    recognition_rule_packs,
    resources,
    waybill_reading,
)


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(collector_runtime.router, tags=["collector-runtime"])
api_router.include_router(export_fields.router, tags=["export-fields"])
api_router.include_router(product_assets.router, tags=["product-assets"])
api_router.include_router(order_row_drafts.router)
api_router.include_router(product_sku_linking.router)
api_router.include_router(recognition_rule_packs.router)
api_router.include_router(waybill_reading.router)
api_router.include_router(platform_accounts.router)

for route_prefix, resource_name, tag in resources.RESOURCE_ROUTES:
    api_router.include_router(
        resources.build_resource_router(resource_name, tag),
        prefix=route_prefix,
        tags=[tag],
    )
