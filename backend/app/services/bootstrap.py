from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import (
    CaptureBatch,
    CaptureTask,
    Collector,
    ExceptionRecord,
    ExportRecord,
    FieldDefinition,
    FieldRoleConfig,
    ImageAsset,
    KeyFieldSet,
    MatchRule,
    OperationLog,
    Product,
    ProductSku,
    RawCaptureRecord,
    ReportBatch,
    ReportLine,
    Role,
    Stall,
    StandardDetail,
    StandardDetailBatch,
    Tenant,
    User,
    UserWorkspace,
    WaybillMode,
    WaybillTemplate,
    WaybillTemplateField,
    Workspace,
)


WORKSPACE_TENANT_MODELS = (
    Role,
    UserWorkspace,
    Collector,
    CaptureTask,
    CaptureBatch,
    RawCaptureRecord,
    StandardDetailBatch,
    StandardDetail,
    FieldDefinition,
    FieldRoleConfig,
    KeyFieldSet,
    MatchRule,
    Product,
    ProductSku,
    Stall,
    ImageAsset,
    ReportBatch,
    ReportLine,
    ExceptionRecord,
    ExportRecord,
    OperationLog,
)


def _backfill_workspace_tenant_ids(db: Session) -> None:
    workspace_tenants = dict(
        db.execute(
            select(Workspace.id, Workspace.tenant_id).where(Workspace.tenant_id.is_not(None))
        ).all()
    )
    for model in WORKSPACE_TENANT_MODELS:
        records = db.scalars(select(model).where(model.tenant_id.is_(None))).all()
        for record in records:
            record.tenant_id = workspace_tenants.get(record.workspace_id)


def seed_initial_data(db: Session) -> None:
    tenant = db.scalars(select(Tenant).where(Tenant.code == "default")).first()
    if tenant is None:
        tenant = Tenant(
            name="Default tenant",
            code="default",
            status="active",
            remark="Initial tenant for local development.",
        )
        db.add(tenant)
        db.flush()

    workspace = db.scalars(select(Workspace).where(Workspace.code == "default")).first()
    if workspace is None:
        workspace = Workspace(
            tenant_id=tenant.id,
            name="Default workspace",
            code="default",
            remark="Initial workspace.",
        )
        db.add(workspace)
        db.flush()
    elif workspace.tenant_id is None:
        workspace.tenant_id = tenant.id

    orphan_workspaces = db.scalars(select(Workspace).where(Workspace.tenant_id.is_(None))).all()
    for orphan_workspace in orphan_workspaces:
        orphan_workspace.tenant_id = tenant.id
    db.flush()
    _backfill_workspace_tenant_ids(db)

    role = db.scalars(
        select(Role).where(Role.workspace_id == workspace.id, Role.name == "system_admin")
    ).first()
    if role is None:
        role = Role(
            tenant_id=workspace.tenant_id,
            workspace_id=workspace.id,
            name="system_admin",
            remark="System administrator.",
        )
        db.add(role)
        db.flush()
    elif role.tenant_id is None:
        role.tenant_id = workspace.tenant_id

    user = db.scalars(select(User).where(User.username == "admin")).first()
    if user is None:
        user = User(
            username="admin",
            display_name="Administrator",
            password_hash=hash_password("admin123"),
            is_enabled=True,
        )
        db.add(user)
        db.flush()

    membership = db.scalars(
        select(UserWorkspace).where(
            UserWorkspace.workspace_id == workspace.id,
            UserWorkspace.user_id == user.id,
        )
    ).first()
    if membership is None:
        db.add(
            UserWorkspace(
                tenant_id=workspace.tenant_id,
                workspace_id=workspace.id,
                user_id=user.id,
                role_id=role.id,
            )
        )
    elif membership.tenant_id is None:
        membership.tenant_id = workspace.tenant_id

    waybill_mode = db.scalars(
        select(WaybillMode).where(WaybillMode.code == "manual_upload")
    ).first()
    if waybill_mode is None:
        db.add(
            WaybillMode(
                name="Manual upload",
                code="manual_upload",
                input_format="excel_row",
                remark="Initial mode for uploaded spreadsheets.",
                is_enabled=True,
            )
        )

    douyin_mode = db.scalars(
        select(WaybillMode).where(WaybillMode.code == "douyin_cloud_print")
    ).first()
    if douyin_mode is None:
        douyin_mode = WaybillMode(
            name="抖店 / CloudPrint",
            code="douyin_cloud_print",
            input_format="print_component_json",
            remark="平台内置解析模式：从 CloudPrintClient 原始 JSON 中抽取抖店面单可读字段。",
            is_enabled=True,
        )
        db.add(douyin_mode)
        db.flush()

    douyin_template = db.scalars(
        select(WaybillTemplate).where(
            WaybillTemplate.waybill_mode_id == douyin_mode.id,
            WaybillTemplate.name == "抖店 CloudPrint 面单解析 v1",
            WaybillTemplate.is_deleted.is_(False),
        )
    ).first()
    if douyin_template is None:
        douyin_template = WaybillTemplate(
            waybill_mode_id=douyin_mode.id,
            name="抖店 CloudPrint 面单解析 v1",
            input_format="json",
            recognition_rules={
                "source_components": ["cloud-print-client"],
                "required_paths": [
                    "task.documents[].contents[].data.trackNo",
                    "task.documents[].contents[].data.orderId",
                ],
            },
            extraction_rules={
                "parser": "builtin.douyin_cloud_print_v1",
                "fields": {
                    "logistics_no": "contents[].data.trackNo",
                    "order_no": "contents[].data.orderId",
                    "shop_name": "contents[].data.shopName",
                    "product_full_text": "contents[].data.productInfo",
                    "product_short_text": "contents[].data.productShortInfo",
                    "product_count_text": "contents[].data.productCount",
                    "spec_text": "contents[].data.productShortInfo",
                    "quantity": "number(contents[].data.productCount)",
                    "custom_product_text": "parser inferred product category from productShortInfo",
                    "custom_sales_attr1_text": "parser inferred sales attribute 1 from productShortInfo",
                    "custom_sales_attr2_text": "parser inferred sales attribute 2 from productShortInfo",
                    "custom_quantity_text": "parser inferred quantity from productCount/productShortInfo",
                },
            },
            is_enabled=True,
            version="1",
        )
        db.add(douyin_template)
        db.flush()

    cainiao_mode = db.scalars(
        select(WaybillMode).where(WaybillMode.code == "cainiao_cloud_print")
    ).first()
    if cainiao_mode is None:
        cainiao_mode = WaybillMode(
            name="菜鸟云打印（遗留）",
            code="cainiao_cloud_print",
            input_format="print_component_json",
            remark="遗留解析模式。已拆分为菜鸟店铺直打和菜鸟 woda 打印平台两类。",
            is_enabled=False,
        )
        db.add(cainiao_mode)
        db.flush()
    else:
        cainiao_mode.name = "菜鸟云打印（遗留）"
        cainiao_mode.remark = "遗留解析模式。已拆分为菜鸟店铺直打和菜鸟 woda 打印平台两类。"
        cainiao_mode.is_enabled = False

    cainiao_template = db.scalars(
        select(WaybillTemplate).where(
            WaybillTemplate.waybill_mode_id == cainiao_mode.id,
            WaybillTemplate.name == "菜鸟云打印面单解析 v1",
            WaybillTemplate.is_deleted.is_(False),
        )
    ).first()
    if cainiao_template is None:
        cainiao_template = WaybillTemplate(
            waybill_mode_id=cainiao_mode.id,
            name="菜鸟云打印面单解析 v1",
            input_format="json",
            recognition_rules={
                "source_components": ["cainiao-cnprint"],
                "required_paths": ["task.documents[].documentID"],
            },
            extraction_rules={
                "parser": "builtin.cainiao_cloud_print_v1",
                "fields": {
                    "raw_document_id": "task.documents[].documentID",
                    "custom_area_text": "CDATA in contents[].printXML",
                    "template_urls": "contents[].templateURL",
                },
            },
            is_enabled=True,
            version="1",
        )
        db.add(cainiao_template)
        db.flush()

    cainiao_direct_mode = db.scalars(
        select(WaybillMode).where(WaybillMode.code == "cainiao_direct_shop")
    ).first()
    if cainiao_direct_mode is None:
        cainiao_direct_mode = WaybillMode(
            name="菜鸟店铺直接打印",
            code="cainiao_direct_shop",
            input_format="print_component_json",
            remark="平台内置解析模式：菜鸟组件中 documentID 为真实运单号，并带 customArea.data 结构化业务字段。",
            is_enabled=True,
        )
        db.add(cainiao_direct_mode)
        db.flush()

    cainiao_direct_template = db.scalars(
        select(WaybillTemplate).where(
            WaybillTemplate.waybill_mode_id == cainiao_direct_mode.id,
            WaybillTemplate.name == "菜鸟店铺直接打印解析 v1",
            WaybillTemplate.is_deleted.is_(False),
        )
    ).first()
    if cainiao_direct_template is None:
        cainiao_direct_template = WaybillTemplate(
            waybill_mode_id=cainiao_direct_mode.id,
            name="菜鸟店铺直接打印解析 v1",
            input_format="json",
            recognition_rules={
                "source_components": ["cainiao-cnprint"],
                "document_id": "real logistics number, e.g. YT7623405796653",
                "required_paths": [
                    "task.documents[].contents[].data.WAIBILLNO_BAR_CODE",
                    "task.documents[].contents[].data.ORDER_ID",
                ],
            },
            extraction_rules={
                "parser": "builtin.cainiao_direct_shop_v1",
                "fields": {
                    "logistics_no": "contents[].data.WAIBILLNO_BAR_CODE",
                    "order_no": "contents[].data.ORDER_ID",
                    "shop_name": "contents[].data.SHOP_NAME",
                    "product_full_text": "contents[].data.ITEM_INFO",
                    "custom_product_text": "parser inferred product category from ITEM_INFO",
                    "custom_sales_attr1_text": "parser inferred sales attribute 1 from ITEM_INFO",
                    "custom_sales_attr2_text": "parser inferred sales attribute 2 from ITEM_INFO or remarks",
                    "custom_quantity_text": "parser inferred quantity from ITEM_TOTAL_COUNT/ITEM_INFO",
                    "buyer_remark": "contents[].data.BUYER_MEMO",
                    "seller_remark": "contents[].data.SELLER_MEMO",
                    "print_time": "contents[].data.PRINT_TIME",
                },
            },
            is_enabled=True,
            version="1",
        )
        db.add(cainiao_direct_template)
        db.flush()

    cainiao_woda_mode = db.scalars(
        select(WaybillMode).where(WaybillMode.code == "cainiao_woda_printxml")
    ).first()
    if cainiao_woda_mode is None:
        cainiao_woda_mode = WaybillMode(
            name="菜鸟 woda 打印平台",
            code="cainiao_woda_printxml",
            input_format="print_component_json",
            remark="平台内置解析模式：菜鸟组件中 documentID 包含 -woda-，标准面单加密，业务内容只作为客户自定义区原文存档。",
            is_enabled=True,
        )
        db.add(cainiao_woda_mode)
        db.flush()

    cainiao_woda_template = db.scalars(
        select(WaybillTemplate).where(
            WaybillTemplate.waybill_mode_id == cainiao_woda_mode.id,
            WaybillTemplate.name == "菜鸟 woda 打印平台解析 v1",
            WaybillTemplate.is_deleted.is_(False),
        )
    ).first()
    if cainiao_woda_template is None:
        cainiao_woda_template = WaybillTemplate(
            waybill_mode_id=cainiao_woda_mode.id,
            name="菜鸟 woda 打印平台解析 v1",
            input_format="json",
            recognition_rules={
                "source_components": ["cainiao-cnprint"],
                "document_id_contains": "-woda-",
                "required_paths": ["task.documents[].contents[].printXML"],
            },
            extraction_rules={
                "parser": "builtin.cainiao_woda_printxml_v1",
                "fields": {
                    "source_platform": "woda",
                    "raw_document_id": "task.documents[].documentID",
                    "encrypted_waybill": "contents[].encryptedData exists",
                    "custom_area_raw_text": "CDATA in contents[].printXML",
                    "custom_area_lines": "CDATA lines in contents[].printXML",
                    "print_template_key": "normalized printXML layout fingerprint",
                    "print_template_source": "printxml_layout",
                    "template_urls": "contents[].templateURL",
                    "sender_masked": "contents[].addData.sender",
                    "recipient_masked": "contents[].addData.recipient",
                },
            },
            is_enabled=True,
            version="1",
        )
        db.add(cainiao_woda_template)
        db.flush()

    template_field_specs = [
        (douyin_template, "logistics_no", "物流单号", 10),
        (douyin_template, "order_no", "平台订单号", 20),
        (douyin_template, "shop_name", "店铺名", 30),
        (douyin_template, "product_short_text", "商品简称", 40),
        (douyin_template, "product_full_text", "商品信息", 50),
        (douyin_template, "spec_text", "规格文本", 60),
        (douyin_template, "quantity", "数量", 70),
        (douyin_template, "custom_product_text", "商品名称", 80),
        (douyin_template, "custom_sales_attr1_text", "销售属性1", 90),
        (douyin_template, "custom_sales_attr2_text", "销售属性2", 100),
        (douyin_template, "custom_quantity_text", "数量文字", 110),
        (cainiao_direct_template, "logistics_no", "物流单号", 10),
        (cainiao_direct_template, "order_no", "平台订单号", 20),
        (cainiao_direct_template, "shop_name", "店铺名", 30),
        (cainiao_direct_template, "product_full_text", "商品信息", 40),
        (cainiao_direct_template, "custom_product_text", "商品名称", 50),
        (cainiao_direct_template, "custom_sales_attr1_text", "销售属性1", 60),
        (cainiao_direct_template, "custom_sales_attr2_text", "销售属性2", 70),
        (cainiao_direct_template, "custom_quantity_text", "数量文字", 80),
        (cainiao_direct_template, "buyer_remark", "买家备注", 90),
        (cainiao_direct_template, "seller_remark", "卖家备注", 100),
        (cainiao_direct_template, "print_time", "打印时间", 110),
        (cainiao_woda_template, "source_platform", "来源平台", 10),
        (cainiao_woda_template, "raw_document_id", "原始文档 ID", 20),
        (cainiao_woda_template, "encrypted_waybill", "标准面单已加密", 30),
        (cainiao_woda_template, "custom_area_raw_text", "客户自定义区原文", 40),
        (cainiao_woda_template, "custom_area_lines", "自定义区行文字", 50),
        (cainiao_woda_template, "print_template_key", "打印模板识别码", 60),
        (cainiao_woda_template, "sender_masked", "发件方脱敏信息", 70),
        (cainiao_woda_template, "recipient_masked", "收件方脱敏信息", 80),
    ]
    for template, field_code, field_name, sort_order in template_field_specs:
        existing_field = db.scalars(
            select(WaybillTemplateField).where(
                WaybillTemplateField.waybill_template_id == template.id,
                WaybillTemplateField.target_field_code == field_code,
                WaybillTemplateField.is_deleted.is_(False),
            )
        ).first()
        if existing_field is None:
            db.add(
                WaybillTemplateField(
                    waybill_template_id=template.id,
                    target_field_code=field_code,
                    extraction_type="builtin",
                    extraction_config={
                        "name": field_name,
                        "parser": template.extraction_rules.get("parser"),
                        "path": template.extraction_rules.get("fields", {}).get(field_code),
                    },
                    sort_order=sort_order,
                )
            )

    db.commit()
