from fastapi import APIRouter

from app.api.routes import auth, collector_runtime, export_fields, health, platform_accounts, product_assets, resources


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(collector_runtime.router, tags=["collector-runtime"])
api_router.include_router(export_fields.router, tags=["export-fields"])
api_router.include_router(product_assets.router, tags=["product-assets"])
api_router.include_router(platform_accounts.router)

for route_prefix, resource_name, tag in resources.RESOURCE_ROUTES:
    api_router.include_router(
        resources.build_resource_router(resource_name, tag),
        prefix=route_prefix,
        tags=[tag],
    )
