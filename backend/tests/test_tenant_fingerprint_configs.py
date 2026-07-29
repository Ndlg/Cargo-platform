from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.routes import tenant_fingerprint_configs as route
from app.core.context import CurrentUser
from app.models import (
    Base,
    RecognitionRulePack,
    TenantFingerprintConfig,
    Workspace,
)


CATALOG = {
    "fingerprints": [
        {
            "code": "CN-ITEM-INFO",
            "name": "菜鸟商品文本型",
            "description": "商品字段",
            "candidate_fields": [
                {"key": "item_info", "label": "商品信息", "default_selected": True},
                {"key": "seller_memo", "label": "卖家备注", "default_selected": False},
            ],
        },
        {
            "code": "CN-PRINT-XML",
            "name": "菜鸟打印 XML 型",
            "description": "打印 XML",
            "candidate_fields": [
                {"key": "print_text", "label": "打印文本", "default_selected": True},
            ],
        },
        {
            "code": "CLOUD-PRODUCT-INFO",
            "name": "云打印商品信息型",
            "description": "云打印商品字段",
            "candidate_fields": [
                {"key": "product_info", "label": "商品信息", "default_selected": True},
            ],
        },
    ]
}


def current_user(tenant_id: int, workspace_id: int) -> CurrentUser:
    return CurrentUser(
        id=tenant_id,
        username=f"tenant-{tenant_id}",
        display_name=f"Tenant {tenant_id}",
        role_names=("administrator",),
        tenant_ids=(tenant_id,),
        workspace_ids=(workspace_id,),
    )


def seeded_db() -> tuple[Session, RecognitionRulePack]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all(
        [
            Workspace(id=1, tenant_id=11, name="A", code="a"),
            Workspace(id=2, tenant_id=22, name="B", code="b"),
            TenantFingerprintConfig(
                tenant_id=11,
                fingerprint_code="CN-ITEM-INFO",
                is_enabled=True,
                selected_fields=["item_info"],
            ),
            TenantFingerprintConfig(
                tenant_id=11,
                fingerprint_code="CN-PRINT-XML",
                is_enabled=False,
                selected_fields=["print_text"],
            ),
            TenantFingerprintConfig(
                tenant_id=22,
                fingerprint_code="CLOUD-PRODUCT-INFO",
                is_enabled=True,
                selected_fields=["product_info"],
            ),
        ]
    )
    pack = RecognitionRulePack(
        tenant_id=11,
        workspace_id=1,
        name="Existing",
        code="existing",
        payload={"parser_policy": {"format_profiles": [{"fingerprint": "sha256:old"}]}},
        status="active",
        is_enabled=True,
    )
    db.add(pack)
    db.commit()
    return db, pack


def test_tenant_only_sees_and_updates_its_authorized_fingerprints(monkeypatch) -> None:
    db, pack = seeded_db()
    original_pack_payload = pack.payload
    monkeypatch.setattr(route, "fingerprint_catalog_with_service", lambda: CATALOG)

    listed = route.list_tenant_fingerprint_configs(
        db=db,
        _current_user=current_user(11, 1),
        workspace_id=1,
    )
    assert [item["code"] for item in listed["fingerprints"]] == ["CN-ITEM-INFO"]

    updated = route.update_tenant_fingerprint_config(
        fingerprint_code="CN-ITEM-INFO",
        request=route.TenantFingerprintConfigUpdate(selected_fields=["seller_memo"]),
        db=db,
        _current_user=current_user(11, 1),
        workspace_id=1,
    )
    assert updated["fingerprint"]["selected_fields"] == ["seller_memo"]
    assert db.scalar(
        select(TenantFingerprintConfig).where(TenantFingerprintConfig.tenant_id == 22)
    ).selected_fields == ["product_info"]
    assert db.get(RecognitionRulePack, pack.id).payload == original_pack_payload
    db.close()


def test_tenant_cannot_select_unknown_field_or_disabled_fingerprint(monkeypatch) -> None:
    db, _pack = seeded_db()
    monkeypatch.setattr(route, "fingerprint_catalog_with_service", lambda: CATALOG)

    try:
        route.update_tenant_fingerprint_config(
            fingerprint_code="CN-ITEM-INFO",
            request=route.TenantFingerprintConfigUpdate(selected_fields=["receiver_address"]),
            db=db,
            _current_user=current_user(11, 1),
            workspace_id=1,
        )
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("unknown fields must be rejected")

    try:
        route.update_tenant_fingerprint_config(
            fingerprint_code="CN-PRINT-XML",
            request=route.TenantFingerprintConfigUpdate(selected_fields=["print_text"]),
            db=db,
            _current_user=current_user(11, 1),
            workspace_id=1,
        )
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("disabled fingerprints must not be tenant-configurable")
    db.close()
