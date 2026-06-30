import os
import uuid
from pathlib import Path


TEST_DB_DIR = Path(__file__).resolve().parents[2] / ".pytest_cache"
TEST_DB_DIR.mkdir(exist_ok=True)
TEST_DB = TEST_DB_DIR / f"product_sku_resource_filters_{uuid.uuid4().hex}.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["SECRET_KEY"] = "product-sku-resource-filters-secret"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ImageAsset, Product, ProductSku, RawCaptureRecord  # noqa: E402


def _headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Workspace-Id": "1"}


def _seed_products_with_skus() -> tuple[int, int]:
    with SessionLocal() as db:
        product_a = Product(
            tenant_id=1,
            workspace_id=1,
            name=f"性能测试商品 A {uuid.uuid4().hex}",
            is_enabled=True,
        )
        product_b = Product(
            tenant_id=1,
            workspace_id=1,
            name=f"性能测试商品 B {uuid.uuid4().hex}",
            is_enabled=True,
        )
        db.add_all([product_a, product_b])
        db.flush()
        db.add_all(
            [
                ProductSku(
                    tenant_id=1,
                    workspace_id=1,
                    product_id=product_a.id,
                    name="A-40",
                    is_enabled=True,
                ),
                ProductSku(
                    tenant_id=1,
                    workspace_id=1,
                    product_id=product_a.id,
                    name="A-41",
                    is_enabled=True,
                ),
                ProductSku(
                    tenant_id=1,
                    workspace_id=1,
                    product_id=product_b.id,
                    name="B-40",
                    is_enabled=True,
                ),
            ]
        )
        db.commit()
        return product_a.id, product_b.id


def test_product_sku_list_can_be_filtered_by_product_id() -> None:
    with TestClient(app) as client:
        headers = _headers(client)
        product_a_id, product_b_id = _seed_products_with_skus()

        response = client.get(
            f"/api/v1/product-skus?limit=2000&product_id={product_a_id}",
            headers=headers,
        )

        assert response.status_code == 200
        rows = response.json()
        assert [row["name"] for row in rows] == ["A-40", "A-41"]
        assert {row["product_id"] for row in rows} == {product_a_id}
        assert product_b_id not in {row["product_id"] for row in rows}


def test_product_sku_list_can_be_filtered_by_product_id_and_keyword() -> None:
    with TestClient(app) as client:
        headers = _headers(client)
        product_a_id, _product_b_id = _seed_products_with_skus()

        response = client.get(
            f"/api/v1/product-skus?limit=2000&product_id={product_a_id}&q=41",
            headers=headers,
        )

        assert response.status_code == 200
        rows = response.json()
        assert [row["name"] for row in rows] == ["A-41"]
        assert {row["product_id"] for row in rows} == {product_a_id}


def test_product_list_can_be_filtered_by_keyword() -> None:
    with TestClient(app) as client:
        headers = _headers(client)
        with SessionLocal() as db:
            db.add_all(
                [
                    Product(
                        tenant_id=1,
                        workspace_id=1,
                        name="Cloudtilt 联名",
                        is_enabled=True,
                    ),
                    Product(
                        tenant_id=1,
                        workspace_id=1,
                        name="VAP 训练鞋",
                        is_enabled=True,
                    ),
                ]
            )
            db.commit()

        response = client.get("/api/v1/products?limit=50&q=Cloud", headers=headers)

        assert response.status_code == 200
        rows = response.json()
        assert [row["name"] for row in rows] == ["Cloudtilt 联名"]


def test_image_asset_list_can_be_filtered_by_keyword() -> None:
    with TestClient(app) as client:
        headers = _headers(client)
        with SessionLocal() as db:
            db.add_all(
                [
                    ImageAsset(
                        tenant_id=1,
                        workspace_id=1,
                        name="Cloudtilt image",
                        file_path="storage/test/cloudtilt.png",
                    ),
                    ImageAsset(
                        tenant_id=1,
                        workspace_id=1,
                        name="VAP image",
                        file_path="storage/test/vap.png",
                    ),
                ]
            )
            db.commit()

        response = client.get(
            "/api/v1/image-assets?limit=2000&q=Cloud",
            headers=headers,
        )

        assert response.status_code == 200
        rows = response.json()
        assert [row["name"] for row in rows] == ["Cloudtilt image"]


def test_raw_capture_record_list_can_be_filtered_by_task_id() -> None:
    with TestClient(app) as client:
        headers = _headers(client)
        with SessionLocal() as db:
            db.add_all(
                [
                    RawCaptureRecord(
                        tenant_id=1,
                        workspace_id=1,
                        task_id=101,
                        document_id="TASK-101-A",
                        payload_format="json",
                        raw_payload='{"task": {"documents": [{"contents": []}]}}',
                    ),
                    RawCaptureRecord(
                        tenant_id=1,
                        workspace_id=1,
                        task_id=202,
                        document_id="TASK-202-A",
                        payload_format="json",
                        raw_payload='{"task": {"documents": [{"contents": []}]}}',
                    ),
                ]
            )
            db.commit()

        response = client.get("/api/v1/raw-capture-records?task_id=101&limit=50", headers=headers)

        assert response.status_code == 200
        rows = response.json()
        assert [row["document_id"] for row in rows] == ["TASK-101-A"]


def teardown_module() -> None:
    from app.core.database import engine

    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()
