from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.context import CurrentUser
from app.core.database import get_db
from app.core.deps import get_current_user, get_workspace_id, require_write
from app.models import ImageAsset, Product, ProductSku
from app.repositories.base import model_to_dict


router = APIRouter()

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _zip_parts(name: str) -> list[str]:
    return [part for part in re.split(r"[\\/]+", name) if part]


def _safe_filename(name: str) -> str:
    filename = Path(name).name
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip() or "image"


def _safe_stem(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "sku"


def _sku_name_from_file(filename: str) -> str:
    stem = Path(filename).stem
    clean = re.sub(r"^SKU图_\d+_", "", stem)
    parts = clean.split("-", 1)
    sku_name = parts[1].strip() if len(parts) > 1 else clean.strip()
    return sku_name or clean or stem


def _mime_type(extension: str) -> str:
    if extension.lower() in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if extension.lower() == ".webp":
        return "image/webp"
    return "image/png"


def _image_storage_dir(workspace_id: int, product_id: int) -> Path:
    return Path(get_settings().storage_root) / str(workspace_id) / "products" / str(product_id) / "sku"


def _store_sku_image(
    *,
    db: Session,
    product: Product,
    workspace_id: int,
    user_id: int,
    sku_name: str,
    filename: str,
    data: bytes,
) -> tuple[ProductSku, str]:
    extension = Path(filename).suffix.lower()
    if extension not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unsupported image file type.")
    if not data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Image file is empty.")

    digest = sha256(data).hexdigest()
    sku = db.scalars(
        select(ProductSku).where(
            ProductSku.workspace_id == workspace_id,
            ProductSku.product_id == product.id,
            ProductSku.name == sku_name,
        )
    ).first()

    if sku is not None and not sku.is_deleted and sku.image_asset_id:
        existing_image = db.scalars(
            select(ImageAsset).where(
                ImageAsset.id == sku.image_asset_id,
                ImageAsset.workspace_id == workspace_id,
                ImageAsset.is_deleted.is_(False),
            )
        ).first()
        if existing_image is not None and existing_image.content_hash == digest:
            sku.is_deleted = False
            sku.is_enabled = True
            return sku, "duplicated"

    product_dir = _image_storage_dir(workspace_id, product.id)
    product_dir.mkdir(parents=True, exist_ok=True)

    target_name = f"{_safe_stem(sku_name)}-{digest[:12]}-{_safe_filename(filename)}"
    target_path = product_dir / target_name
    target_path.write_bytes(data)

    image = ImageAsset(
        tenant_id=product.tenant_id,
        workspace_id=workspace_id,
        name=f"{product.name}-{sku_name}",
        file_path=str(target_path),
        content_hash=digest,
        mime_type=_mime_type(extension),
        file_size=len(data),
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(image)
    db.flush()

    if sku is None:
        sku_count = db.scalars(
            select(ProductSku).where(
                ProductSku.workspace_id == workspace_id,
                ProductSku.product_id == product.id,
                ProductSku.is_deleted.is_(False),
            )
        ).all()
        sku = ProductSku(
            tenant_id=product.tenant_id,
            workspace_id=workspace_id,
            product_id=product.id,
            name=sku_name,
            image_asset_id=image.id,
            sort_order=len(sku_count) + 1,
            is_enabled=True,
            created_by=user_id,
            updated_by=user_id,
        )
        db.add(sku)
        action = "imported"
    else:
        was_deleted = sku.is_deleted
        sku.is_deleted = False
        sku.is_enabled = True
        sku.image_asset_id = image.id
        sku.updated_by = user_id
        action = "imported" if was_deleted else "updated"
    db.flush()
    return sku, action


@router.get("/image-assets/{image_asset_id}/content")
def get_image_asset_content(
    image_asset_id: int,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> FileResponse:
    image = db.scalars(
        select(ImageAsset).where(
            ImageAsset.id == image_asset_id,
            ImageAsset.workspace_id == workspace_id,
            ImageAsset.is_deleted.is_(False),
        )
    ).first()
    if image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image asset not found.")

    storage_root = Path(get_settings().storage_root).resolve()
    image_path = Path(image.file_path).resolve()
    if not image_path.is_relative_to(storage_root) or not image_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image file not found.")

    return FileResponse(
        image_path,
        media_type=image.mime_type or "application/octet-stream",
        filename=image_path.name,
    )


@router.post("/products/{product_id}/sku-zip", status_code=status.HTTP_201_CREATED)
async def upload_product_sku_zip(
    product_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, object]:
    product = db.scalars(
        select(Product).where(
            Product.id == product_id,
            Product.workspace_id == workspace_id,
            Product.is_deleted.is_(False),
        )
    ).first()
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")

    content = await file.read()
    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid zip file.") from exc

    imported = 0
    updated = 0
    duplicated = 0
    skipped = 0
    sku_records: list[dict[str, object]] = []
    seen_sku_names: set[str] = set()

    with archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            parts = _zip_parts(entry.filename)
            if len(parts) < 2 or parts[0] != "SKU图":
                skipped += 1
                continue
            filename = _safe_filename(parts[-1])
            extension = Path(filename).suffix.lower()
            if extension not in IMAGE_EXTENSIONS:
                skipped += 1
                continue

            sku_name = _sku_name_from_file(filename)
            if sku_name in seen_sku_names:
                duplicated += 1
                continue
            seen_sku_names.add(sku_name)

            sku, action = _store_sku_image(
                db=db,
                product=product,
                workspace_id=workspace_id,
                user_id=current_user.id,
                sku_name=sku_name,
                filename=filename,
                data=archive.read(entry),
            )
            if action == "imported":
                imported += 1
            elif action == "updated":
                updated += 1
            else:
                duplicated += 1
            sku_records.append(model_to_dict(sku))

    db.commit()
    return {
        "product": model_to_dict(product),
        "imported": imported,
        "updated": updated,
        "duplicated": duplicated,
        "skipped": skipped,
        "skus": sku_records,
    }


@router.post("/products/{product_id}/sku-image", status_code=status.HTTP_201_CREATED)
async def upload_product_sku_image(
    product_id: int,
    sku_name: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, object]:
    product = db.scalars(
        select(Product).where(
            Product.id == product_id,
            Product.workspace_id == workspace_id,
            Product.is_deleted.is_(False),
        )
    ).first()
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")

    clean_sku_name = sku_name.strip()
    if not clean_sku_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="SKU name is required.")

    filename = _safe_filename(file.filename or f"{clean_sku_name}.png")
    sku, action = _store_sku_image(
        db=db,
        product=product,
        workspace_id=workspace_id,
        user_id=current_user.id,
        sku_name=clean_sku_name,
        filename=filename,
        data=await file.read(),
    )
    db.commit()
    return {
        "product": model_to_dict(product),
        "sku": model_to_dict(sku),
        "status": action,
    }
