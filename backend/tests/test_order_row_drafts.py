import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = REPO_ROOT / "services" / "waybill-parser"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

TEST_DB = Path(__file__).resolve().parent / "order_row_drafts_test.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["SECRET_KEY"] = "test-secret"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import RawCaptureRecord, RecognitionRulePack, StandardDetail  # noqa: E402
from app.services import order_row_reader as order_row_reader_service  # noqa: E402
from service_app.order_row_engine import (  # noqa: E402
    draft_rows_from_payload,
    draft_rows_from_standard_detail_values,
    draft_rows_from_waybill_sample,
    order_row_draft_summary,
)


ACTIVE_RULE_PACK_PAYLOAD = {
    "contract_version": "recognition_rule_pack_v1",
    "pack": {
        "code": "test-shoes",
        "name": "测试鞋类规则包",
        "version": "1.0.0",
    },
    "parser_policy": {
        "requires_active_rule_pack": True,
        "order_row_parser": "shoe_waybill_v1",
        "quantity": {"default_if_missing": 1},
    },
}


def activate_test_rule_pack() -> None:
    with SessionLocal() as db:
        db.query(RecognitionRulePack).filter(RecognitionRulePack.workspace_id == 1).update(
            {"is_enabled": False, "status": "inactive"}
        )
        existing = db.query(RecognitionRulePack).filter(
            RecognitionRulePack.workspace_id == 1,
            RecognitionRulePack.code == "test-shoes",
            RecognitionRulePack.is_deleted.is_(False),
        ).first()
        if existing is None:
            existing = RecognitionRulePack(
                tenant_id=1,
                workspace_id=1,
                name="测试鞋类规则包",
                code="test-shoes",
                version="1.0.0",
                payload=ACTIVE_RULE_PACK_PAYLOAD,
                status="active",
                is_enabled=True,
            )
            db.add(existing)
        else:
            existing.status = "active"
            existing.is_enabled = True
            existing.payload = ACTIVE_RULE_PACK_PAYLOAD
        db.commit()


def parser_payload_from_parents(task_id: int, parents: list) -> dict:
    return {
        "contract_version": "order_row_drafts_v1",
        "task_id": task_id,
        "summary": order_row_draft_summary(parents),
        "parents": [parent.as_dict() for parent in parents],
        "rows": [row.as_dict() for parent in parents for row in parent.rows],
    }


def use_order_row_parser_service_stub(monkeypatch) -> None:
    def fake_parse_order_row_drafts_with_service(
        *,
        task_id: int,
        standard_details: list[dict] | None = None,
        raw_records: list[dict] | None = None,
        waybill_samples: list[dict] | None = None,
        rule_pack: dict,
    ) -> dict:
        assert raw_records in (None, [])
        assert rule_pack["pack"]["code"] == "test-shoes"
        parents = [
            draft_rows_from_standard_detail_values(
                parser_input["field_values"],
                standard_detail_id=parser_input["standard_detail_id"],
                parent_sequence=parser_input["parent_sequence"],
            )
            for parser_input in (standard_details or [])
        ]
        parents.extend(
            draft_rows_from_waybill_sample(
                parser_input,
                parent_sequence=parser_input["parent_sequence"],
            )
            for parser_input in (waybill_samples or [])
        )
        return parser_payload_from_parents(task_id, parents)

    monkeypatch.setattr(order_row_reader_service, "waybill_parser_service_enabled", lambda: True, raising=False)
    monkeypatch.setattr(
        order_row_reader_service,
        "parse_order_row_drafts_with_service",
        fake_parse_order_row_drafts_with_service,
        raising=False,
    )


def waybill_sample_with_original_lines(
    lines: list[tuple[str, str]],
    *,
    raw_record_id: int = 1001,
    task_id: int = 19,
) -> dict:
    blocks = []
    sample_lines = []
    for order, (source_path, text) in enumerate(lines):
        sample_lines.append(text)
        blocks.append(
            {
                "text": text,
                "block_kind": "original",
                "order": order,
                "source_path": source_path,
            }
        )
    return {
        "raw_record_id": raw_record_id,
        "task_id": task_id,
        "source_component": "cainiao-cnprint",
        "source_index": str(raw_record_id),
        "sample_text": "\n".join(sample_lines),
        "text_blocks": blocks,
    }


def test_waybill_sample_uses_labeled_remark_line_to_replace_default_attrs() -> None:
    sample = waybill_sample_with_original_lines(
        [
            ("task.documents[0].contents[1].printXML.cdata[0]", "秒67 175，,默认，默认*1"),
            ("task.documents[0].contents[1].printXML.cdata[1]", "颜色分类:Cloudtilt纯白;鞋码:39"),
        ]
    )

    result = draft_rows_from_waybill_sample(sample, parent_sequence=93)

    assert result.child_count == 1
    row = result.rows[0]
    assert row.product == "秒67 175"
    assert row.sales_attr1 == "Cloudtilt纯白"
    assert row.sales_attr2 == "39"
    assert row.quantity == 1
    assert row.status == "draft"


def test_waybill_sample_product_line_followed_by_free_remark_stays_one_row() -> None:
    sample = waybill_sample_with_original_lines(
        [
            ("task.documents[0].contents[1].printXML.cdata[0]", "秒55 f，,灰蓝红，38.5*1"),
            ("task.documents[0].contents[1].printXML.cdata[1]", "拼接"),
        ],
        raw_record_id=2638,
    )

    result = draft_rows_from_waybill_sample(sample, parent_sequence=53)

    assert result.child_count == 1
    row = result.rows[0]
    assert row.product == "秒55 f"
    assert row.sales_attr1 == "灰蓝红"
    assert row.sales_attr2 == "38.5"
    assert row.quantity == 1
    assert row.remark == "拼接"
    assert row.status == "draft"


def test_waybill_sample_product_line_followed_by_shipping_remark_stays_one_row() -> None:
    sample = waybill_sample_with_original_lines(
        [
            ("task.documents[0].contents[1].printXML.cdata[0]", "低帮灰红，38.5"),
            (
                "task.documents[0].contents[1].printXML.cdata[0]",
                "2026登山鞋越野低帮LOW复古户外休闲鞋机能男鞋女鞋防水耐磨ACG*1",
            ),
            ("task.documents[0].contents[1].printXML.cdata[1]", "用户物流催促，请加急处理！"),
        ],
        raw_record_id=7134,
    )

    result = draft_rows_from_waybill_sample(sample, parent_sequence=134)

    assert result.child_count == 1
    row = result.rows[0]
    assert row.product == "2026登山鞋越野低帮LOW复古户外休闲鞋机能男鞋女鞋防水耐磨ACG"
    assert row.sales_attr1 == "低帮灰红"
    assert row.sales_attr2 == "38.5"
    assert row.quantity == 1
    assert row.remark == "用户物流催促，请加急处理！"
    assert row.status == "draft"


def test_waybill_sample_two_full_item_lines_are_not_collapsed_into_one_row() -> None:
    sample = waybill_sample_with_original_lines(
        [
            ("task.documents[0].contents[1].printXML.cdata[0]", "范30 E，,C5-冰白，40*1"),
            ("task.documents[0].contents[1].printXML.cdata[0]", "范30 d，,X4淡绿，39*1"),
        ],
        raw_record_id=2297,
    )

    result = draft_rows_from_waybill_sample(sample, parent_sequence=21)

    assert result.child_count == 2
    assert [row.product for row in result.rows] == ["范30 E", "范30 d"]
    assert [row.sales_attr1 for row in result.rows] == ["C5-冰白", "X4淡绿"]
    assert [row.sales_attr2 for row in result.rows] == ["40", "39"]
    assert [row.quantity for row in result.rows] == [1, 1]
    assert all(row.status == "draft" for row in result.rows)


def test_waybill_sample_pairs_attr_line_with_following_product_line() -> None:
    sample = waybill_sample_with_original_lines(
        [
            ("task.documents[1].contents[1].printXML.cdata[0]", "5.0二代灰黑，42"),
            ("task.documents[1].contents[1].printXML.cdata[0]", "2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身*1"),
        ],
        raw_record_id=1018,
    )

    result = draft_rows_from_waybill_sample(sample, parent_sequence=119)

    assert result.child_count == 1
    row = result.rows[0]
    assert row.product == "2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身"
    assert row.sales_attr1 == "5.0二代灰黑"
    assert row.sales_attr2 == "42"
    assert row.quantity == 1


def test_waybill_sample_ignores_cancelled_placeholder_and_pairs_remaining_item() -> None:
    sample = waybill_sample_with_original_lines(
        [
            ("task.documents[0].contents[1].printXML.cdata[0]", "xxxxxxxxxx"),
            ("task.documents[0].contents[1].printXML.cdata[0]", "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX*1"),
            ("task.documents[0].contents[1].printXML.cdata[0]", "C5-冰白，39"),
            ("task.documents[0].contents[1].printXML.cdata[0]", "【牛货】春秋网面透气训练C5男女款户外运动鞋百搭休闲联名跑步鞋*1"),
        ],
        raw_record_id=1017,
    )

    result = draft_rows_from_waybill_sample(sample, parent_sequence=117)

    assert result.child_count == 1
    row = result.rows[0]
    assert row.product == "牛货-春秋网面透气训练C5男女款户外运动鞋百搭休闲联名跑步鞋"
    assert row.sales_attr1 == "C5-冰白"
    assert row.sales_attr2 == "39"
    assert row.quantity == 1


def test_waybill_sample_item_info_multiple_lines_ignores_auto_font_size() -> None:
    sample = waybill_sample_with_original_lines(
        [
            (
                "task.documents[2].contents[1].data.ITEM_INFO",
                "2026超轻减震网面鞋训练鞋赤足女鞋健身鞋动鞋男鞋跑步鞋透气4代 4.0二代灰色;38 【1件】",
            ),
            (
                "task.documents[2].contents[1].data.ITEM_INFO",
                "2026超轻减震网面鞋训练鞋赤足女鞋健身鞋动鞋男鞋跑步鞋透气4代 4.0二代黑白;38 【1件】",
            ),
            ("task.documents[2].contents[1].data.itemInfoFontSize", "auto"),
        ],
        raw_record_id=1005,
    )

    result = draft_rows_from_waybill_sample(sample, parent_sequence=105)

    assert result.child_count == 2
    assert [row.sales_attr1 for row in result.rows] == ["4.0二代灰色", "4.0二代黑白"]
    assert [row.sales_attr2 for row in result.rows] == ["38", "38"]
    assert all(row.product == "2026超轻减震网面鞋训练鞋赤足女鞋健身鞋动鞋男鞋跑步鞋透气4代" for row in result.rows)
    assert all(row.quantity == 1 for row in result.rows)


def test_waybill_sample_ignores_buyer_memo_when_item_info_is_available() -> None:
    sample = waybill_sample_with_original_lines(
        [
            (
                "task.documents[9].contents[1].data.ITEM_INFO",
                "2026赤足跑步鞋男鞋女鞋休闲鞋网面透气夏季软底缓震体考健身轻质 5.0灰橙;40 【1件】",
            ),
            (
                "task.documents[9].contents[1].data.BUYER_MEMO",
                "此单为官方转运订单；不同订单不可合包发货，快递单号需上传正确。发货规则https://p.tb.cn/_4QpZE71GxG2",
            ),
        ],
        raw_record_id=1015,
    )

    result = draft_rows_from_waybill_sample(sample, parent_sequence=115)

    assert result.child_count == 1
    row = result.rows[0]
    assert row.product == "2026赤足跑步鞋男鞋女鞋休闲鞋网面透气夏季软底缓震体考健身轻质"
    assert row.sales_attr1 == "5.0灰橙"
    assert row.sales_attr2 == "40"
    assert row.quantity == 1


def test_multi_product_waybill_becomes_multiple_child_waybills() -> None:
    product_line = (
        "【登山鞋涉水鞋防水机能户外徒步男鞋女鞋休闲防滑溯溪越野鞋跑步鞋】低帮黑白 42 1件；"
        "【登山鞋涉水鞋防水机能户外徒步男鞋女鞋休闲防滑溯溪越野鞋跑步鞋】低帮深卡其 39 1件；"
        "【登山鞋涉水鞋防水机能户外徒步男鞋女鞋休闲防滑溯溪越野鞋跑步鞋】低帮浅绿 36 1件"
    )
    payload = {
        "task": {
            "documents": [
                {
                    "documentID": "DOC-1",
                    "contents": [
                        {
                            "data": {
                                "productCount": "3件",
                                "productInfo": product_line,
                                "productShortInfo": product_line,
                            }
                        }
                    ],
                }
            ]
        }
    }

    result = draft_rows_from_payload(
        payload,
        raw_record_id=24,
        task_id=18,
        source_component="cloud-print-client",
        source_index="24",
    )

    assert result.parent_label == "第1批-第24单"
    assert result.child_count == 3
    assert [row.child_label for row in result.rows] == [
        "第1批-第24单-子1",
        "第1批-第24单-子2",
        "第1批-第24单-子3",
    ]
    assert [row.product for row in result.rows] == [
        "登山鞋涉水鞋防水机能户外徒步男鞋女鞋休闲防滑溯溪越野鞋跑步鞋",
        "登山鞋涉水鞋防水机能户外徒步男鞋女鞋休闲防滑溯溪越野鞋跑步鞋",
        "登山鞋涉水鞋防水机能户外徒步男鞋女鞋休闲防滑溯溪越野鞋跑步鞋",
    ]
    assert [row.sales_attr1 for row in result.rows] == ["低帮黑白", "低帮深卡其", "低帮浅绿"]
    assert [row.sales_attr2 for row in result.rows] == ["42", "39", "36"]
    assert [row.quantity for row in result.rows] == [1, 1, 1]
    assert all(row.status == "draft" for row in result.rows)


def test_single_product_waybill_becomes_one_child_waybill_with_numeric_quantity() -> None:
    payload = {
        "task": {
            "documents": [
                {
                    "documentID": "DOC-2",
                    "contents": [
                        {
                            "data": {
                                "productCount": "1件",
                                "productInfo": "【现货新款4.0鞋子跑步鞋公路四代男鞋女鞋网面透气系带跑道5.0】4.0灰蓝 42.5 1件",
                                "productShortInfo": "【现货新款4.0鞋子跑步鞋公路四代男鞋女鞋网面透气系带跑道5.0】4.0灰蓝 42.5 1件",
                            }
                        }
                    ],
                }
            ]
        }
    }

    result = draft_rows_from_payload(
        payload,
        raw_record_id=16,
        task_id=18,
        source_component="cloud-print-client",
        source_index="16",
    )

    assert result.child_count == 1
    assert result.rows[0].child_label == "第1批-第16单-子1"
    assert result.rows[0].product == "现货新款4.0鞋子跑步鞋公路四代男鞋女鞋网面透气系带跑道5.0"
    assert result.rows[0].sales_attr1 == "4.0灰蓝"
    assert result.rows[0].sales_attr2 == "42.5"
    assert result.rows[0].quantity == 1


def test_raw_record_source_index_does_not_leak_as_business_waybill_number() -> None:
    payload = {
        "task": {
            "documents": [
                {
                    "documentID": "DOC-raw-id",
                    "contents": [
                        {
                            "data": {
                                "productCount": "1件",
                                "productInfo": "2025新款网面女鞋男鞋情侣透气跑步鞋 5.0二代灰色 38.5 1件",
                                "productShortInfo": "2025新款网面女鞋男鞋情侣透气跑步鞋 5.0二代灰色 38.5 1件",
                            }
                        }
                    ],
                }
            ]
        }
    }

    result = draft_rows_from_payload(
        payload,
        raw_record_id=7132,
        task_id=19,
        source_component="cainiao-cnprint",
        source_index="7132",
        parent_sequence=8,
    )

    assert result.parent_label == "第1批-第8单"
    assert result.rows[0].child_label == "第1批-第8单-子1"


def test_standard_detail_two_line_waybill_prefers_business_product_line() -> None:
    field_values = {
        "capture_task_id": 18,
        "raw_record_id": 123,
        "document_sequence": 1,
        "source_component": "cainiao-cnprint",
        "source_index": "7113",
        "product_short_text": "5.0二代灰色，38.5\n2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身*1",
        "product_full_text": "5.0二代灰色，38.5\n2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身*1",
    }

    result = draft_rows_from_standard_detail_values(
        field_values,
        standard_detail_id=731,
        parent_sequence=1,
    )

    assert result.parent_label == "第1批-第1单"
    assert result.child_count == 1
    assert result.rows[0].child_label == "第1批-第1单-子1"
    assert result.rows[0].product == "2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身"
    assert result.rows[0].sales_attr1 == "5.0二代灰色"
    assert result.rows[0].sales_attr2 == "38.5"
    assert result.rows[0].quantity == 1
    assert result.rows[0].status == "draft"


def test_standard_detail_two_line_product_then_labeled_attrs_stays_one_child_waybill() -> None:
    result = draft_rows_from_standard_detail_values(
        {
            "capture_task_id": 18,
            "raw_record_id": 156,
            "source_component": "cainiao-cnprint",
            "source_index": "2231",
            "product_short_text": "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋，,*1\n颜色分类:黑灰;尺码:42.5",
        },
        standard_detail_id=736,
        parent_sequence=56,
    )

    assert result.child_count == 1
    assert result.rows[0].child_label == "第1批-第56单-子1"
    assert result.rows[0].product == "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋"
    assert result.rows[0].sales_attr1 == "黑灰"
    assert result.rows[0].sales_attr2 == "42.5"
    assert result.rows[0].quantity == 1
    assert result.rows[0].status == "draft"

    no_quantity_result = draft_rows_from_standard_detail_values(
        {
            "capture_task_id": 18,
            "raw_record_id": 157,
            "source_component": "cainiao-cnprint",
            "source_index": "2232",
            "product_short_text": "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋\n颜色分类:黑灰;尺码:42.5",
        },
        standard_detail_id=737,
        parent_sequence=57,
    )

    assert no_quantity_result.child_count == 1
    assert no_quantity_result.rows[0].product == "【流放】男鞋针织跑步鞋全掌气垫女鞋白黑舒适休闲鞋运动鞋健身鞋"
    assert no_quantity_result.rows[0].sales_attr1 == "黑灰"
    assert no_quantity_result.rows[0].sales_attr2 == "42.5"
    assert no_quantity_result.rows[0].quantity == 1
    assert no_quantity_result.rows[0].status == "draft"


def test_standard_detail_product_lines_pair_with_labeled_attr_remark_lines() -> None:
    result = draft_rows_from_standard_detail_values(
        {
            "capture_task_id": 18,
            "raw_record_id": 168,
            "source_component": "cainiao-cnprint",
            "source_index": "2588",
            "product_short_text": (
                "秒67 175，,默认，可调节*1\n"
                "秒67 175，,默认，默认*1\n"
                "颜色分类:Cloudtilt聯名2代白黑;鞋码:41\n"
                "颜色分类:Cloudtilt蓝灰;鞋码:42"
            ),
        },
        standard_detail_id=738,
        parent_sequence=131,
    )

    assert result.child_count == 2
    assert [row.child_label for row in result.rows] == [
        "第1批-第131单-子1",
        "第1批-第131单-子2",
    ]
    assert [row.product for row in result.rows] == ["秒67 175", "秒67 175"]
    assert [row.sales_attr1 for row in result.rows] == ["Cloudtilt聯名2代白黑", "Cloudtilt蓝灰"]
    assert [row.sales_attr2 for row in result.rows] == ["41", "42"]
    assert [row.quantity for row in result.rows] == [1, 1]
    assert all(row.status == "draft" for row in result.rows)


def test_standard_detail_comma_attrs_preserve_non_bracket_product_name() -> None:
    cases = [
        (
            "1小79 备1 of2025，白黑色，40",
            "1小79 备1 of2025",
            "白黑色",
            "40",
        ),
        (
            "范33 带木one帆布kw，木村-3M反光，39",
            "范33 带木one帆布kw",
            "木村-3M反光",
            "39",
        ),
        (
            "秒61 a11，,11代低帮大魔王，45*1 运单号：[YT7626369927327,YT7626368840876]",
            "秒61 a11",
            "11代低帮大魔王",
            "45",
        ),
        (
            "秒68 ac，低帮荧光绿，40*1",
            "秒68 ac",
            "低帮荧光绿",
            "40",
        ),
    ]

    for source_text, product, sales_attr1, sales_attr2 in cases:
        result = draft_rows_from_standard_detail_values(
            {
                "capture_task_id": 18,
                "raw_record_id": 124,
                "source_component": "cainiao-cnprint",
                "source_index": "7114",
                "product_short_text": source_text,
                "product_count_text": "1件",
            },
            standard_detail_id=732,
            parent_sequence=1,
        )

        assert result.child_count == 1
        assert result.rows[0].product == product
        assert result.rows[0].sales_attr1 == sales_attr1
        assert result.rows[0].sales_attr2 == sales_attr2
        assert result.rows[0].quantity == 1
        assert "运单号" not in result.rows[0].image_match_text


def test_standard_detail_repeated_quantity_items_without_brackets_split_into_rows() -> None:
    cases = [
        (
            "5.0范51，5.0二代全黑，44.5*1 5.0范51，5.0二代白紫，44.5*1 5.0范51，5.0二代黑，44.5*1",
            ["5.0范51", "5.0范51", "5.0范51"],
            ["5.0二代全黑", "5.0二代白紫", "5.0二代黑"],
            ["44.5", "44.5", "44.5"],
        ),
        (
            "秒47 AC，低帮黑红，44*1 秒47 AC，低帮黑色，44*1 秒47 AC，低帮黑绿，44*1",
            ["秒47 AC", "秒47 AC", "秒47 AC"],
            ["低帮黑红", "低帮黑色", "低帮黑绿"],
            ["44", "44", "44"],
        ),
    ]

    for source_text, products, sales_attr1_values, sales_attr2_values in cases:
        result = draft_rows_from_standard_detail_values(
            {
                "capture_task_id": 18,
                "raw_record_id": 180,
                "source_component": "cloud-print-client",
                "source_index": "7180",
                "product_short_text": source_text,
            },
            standard_detail_id=780,
            parent_sequence=26,
        )

        assert result.child_count == 3
        assert [row.product for row in result.rows] == products
        assert [row.sales_attr1 for row in result.rows] == sales_attr1_values
        assert [row.sales_attr2 for row in result.rows] == sales_attr2_values
        assert [row.quantity for row in result.rows] == [1, 1, 1]
        assert all(row.status == "draft" for row in result.rows)


def test_standard_detail_repeated_attr_size_product_items_split_and_parse_fields() -> None:
    result = draft_rows_from_standard_detail_values(
        {
            "capture_task_id": 18,
            "raw_record_id": 224,
            "source_component": "cloud-print-client",
            "source_index": "7224",
            "product_short_text": (
                "5.0二代灰色，40 2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身*1 "
                "5.0二代灰黑，40 2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身*1 "
                "5.0二代灰黑，40 2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身*1"
            ),
        },
        standard_detail_id=781,
        parent_sequence=224,
    )

    assert result.child_count == 3
    assert [row.product for row in result.rows] == [
        "2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身",
        "2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身",
        "2025新款网面女鞋男鞋情侣透气跑步鞋休闲时尚运动鞋健身",
    ]
    assert [row.sales_attr1 for row in result.rows] == ["5.0二代灰色", "5.0二代灰黑", "5.0二代灰黑"]
    assert [row.sales_attr2 for row in result.rows] == ["40", "40", "40"]
    assert [row.quantity for row in result.rows] == [1, 1, 1]
    assert all(row.status == "draft" for row in result.rows)


def test_standard_detail_product_trailing_attr_slash_size_is_cleaned() -> None:
    product = "夏季赤足5.0轻便透气休闲运动跑步鞋男女通用健身体考鞋赤足4.0"
    result = draft_rows_from_standard_detail_values(
        {
            "capture_task_id": 18,
            "raw_record_id": 174,
            "source_component": "cloud-print-client",
            "source_index": "2372",
            "product_short_text": f"{product} 5.0二代灰黑/40 1 件",
        },
        standard_detail_id=782,
        parent_sequence=138,
    )

    assert result.child_count == 1
    row = result.rows[0]
    assert row.product == product
    assert row.sales_attr1 == "5.0二代灰黑"
    assert row.sales_attr2 == "40"
    assert row.quantity == 1
    assert row.status == "draft"


def test_standard_detail_special_wechat_text_is_normal_special_row() -> None:
    for source_text in ("微信--阿迪银灰色 42，，", "微信--阿迪银灰色 42 ，,*1", "微信至尚--鬼冢虎 白浅蓝41.5，,*1"):
        result = draft_rows_from_standard_detail_values(
            {
                "capture_task_id": 18,
                "raw_record_id": 147,
                "source_component": "cainiao-cnprint",
                "source_index": "7117",
                "product_short_text": source_text,
                "product_count_text": "1件",
            },
            standard_detail_id=733,
            parent_sequence=47,
        )

        assert result.child_count == 1
        assert result.rows[0].status == "special"
        assert result.rows[0].review_reason == "wechat_special_waybill"
        assert result.rows[0].product == ""
        assert result.rows[0].sales_attr1 == ""
        assert result.rows[0].sales_attr2 == ""
        assert result.rows[0].quantity is None
        assert result.rows[0].image_match_text == source_text


def test_standard_detail_label_only_attrs_keep_attrs_without_fake_product() -> None:
    result = draft_rows_from_standard_detail_values(
        {
            "capture_task_id": 18,
            "raw_record_id": 175,
            "source_component": "cainiao-cnprint",
            "source_index": "2592",
            "product_short_text": "颜色分类:4.0二代灰色;鞋码:40.5，,*1",
        },
        standard_detail_id=739,
        parent_sequence=139,
    )

    assert result.child_count == 1
    assert result.rows[0].status == "draft"
    assert result.rows[0].product == ""
    assert result.rows[0].sales_attr1 == "4.0二代灰色"
    assert result.rows[0].sales_attr2 == "40.5"
    assert result.rows[0].quantity == 1
    assert result.rows[0].image_match_text == "4.0二代灰色 40.5 1"


def test_standard_detail_product_then_label_attrs_is_not_reversed_by_fallback_quantity() -> None:
    result = draft_rows_from_standard_detail_values(
        {
            "capture_task_id": 18,
            "raw_record_id": 169,
            "source_component": "cainiao-cnprint",
            "source_index": "2589",
            "product_short_text": "秒67 175，,默认，默认*1\n颜色分类:X4淡绿;鞋码:39",
            "product_count_text": "4",
        },
        standard_detail_id=740,
        parent_sequence=133,
    )

    assert result.child_count == 1
    assert result.rows[0].status == "draft"
    assert result.rows[0].product == "秒67 175"
    assert result.rows[0].sales_attr1 == "X4淡绿"
    assert result.rows[0].sales_attr2 == "39"
    assert result.rows[0].quantity == 1


def test_standard_detail_space_semicolon_attrs_strip_bracketed_quantity() -> None:
    result = draft_rows_from_standard_detail_values(
        {
            "capture_task_id": 18,
            "raw_record_id": 148,
            "source_component": "cainiao-cnprint",
            "source_index": "7118",
            "product_short_text": "2026超轻减震网面鞋训练鞋赤足女鞋健身鞋动鞋男鞋跑步鞋透气4代 4.0黑白灰;42 【1件】",
        },
        standard_detail_id=734,
        parent_sequence=48,
    )

    assert result.child_count == 1
    assert result.rows[0].status == "draft"
    assert result.rows[0].product == "2026超轻减震网面鞋训练鞋赤足女鞋健身鞋动鞋男鞋跑步鞋透气4代"
    assert result.rows[0].sales_attr1 == "4.0黑白灰"
    assert result.rows[0].sales_attr2 == "42"
    assert result.rows[0].quantity == 1


def test_standard_detail_size_attr2_strips_purchase_hint_noise() -> None:
    result = draft_rows_from_standard_detail_values(
        {
            "capture_task_id": 18,
            "raw_record_id": 134,
            "source_component": "cainiao-cnprint",
            "source_index": "7134",
            "product_short_text": "新款特价黑色反光一星木村Low麂皮男鞋女鞋滑板鞋158477C 158369C，黑色反光一星158477C，44(偏大一码，建议拍小)",
            "product_count_text": "1件",
        },
        standard_detail_id=735,
        parent_sequence=34,
    )

    assert result.child_count == 1
    assert result.rows[0].status == "draft"
    assert result.rows[0].product == "新款特价黑色反光一星木村Low麂皮男鞋女鞋滑板鞋158477C 158369C"
    assert result.rows[0].sales_attr1 == "黑色反光一星158477C"
    assert result.rows[0].sales_attr2 == "44"
    assert result.rows[0].quantity == 1


def test_recognition_rule_pack_import_activate_export_api() -> None:
    payload = {
        **ACTIVE_RULE_PACK_PAYLOAD,
        "pack": {
            "code": "api-shoes",
            "name": "API 导入鞋类规则包",
            "version": "1.0.0",
        },
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        imported = client.post(
            "/api/v1/recognition-rule-packs/import",
            headers=headers,
            json={"payload": payload, "activate": True},
        )
        assert imported.status_code == 201
        imported_pack = imported.json()["pack"]
        assert imported_pack["code"] == "api-shoes"
        assert imported_pack["status"] == "active"
        assert imported_pack["is_enabled"] is True

        listing = client.get("/api/v1/recognition-rule-packs", headers=headers)
        assert listing.status_code == 200
        assert listing.json()["active_pack"]["code"] == "api-shoes"

        exported = client.get(f"/api/v1/recognition-rule-packs/{imported_pack['id']}/export", headers=headers)
        assert exported.status_code == 200
        assert exported.json()["payload"]["pack"]["code"] == "api-shoes"

        deactivated = client.post(f"/api/v1/recognition-rule-packs/{imported_pack['id']}/deactivate", headers=headers)
        assert deactivated.status_code == 200
        assert deactivated.json()["pack"]["status"] == "inactive"
        assert deactivated.json()["pack"]["is_enabled"] is False

        listing = client.get("/api/v1/recognition-rule-packs", headers=headers)
        assert listing.status_code == 200
        assert listing.json()["active_pack"] is None
        assert any(pack["code"] == "api-shoes" for pack in listing.json()["packs"])

        reactivated = client.post(f"/api/v1/recognition-rule-packs/{imported_pack['id']}/activate", headers=headers)
        assert reactivated.status_code == 200
        assert reactivated.json()["pack"]["status"] == "active"

        deleted = client.delete(f"/api/v1/recognition-rule-packs/{imported_pack['id']}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        listing = client.get("/api/v1/recognition-rule-packs", headers=headers)
        assert listing.status_code == 200
        assert listing.json()["active_pack"] is None
        assert all(pack["code"] != "api-shoes" for pack in listing.json()["packs"])

        exported = client.get(f"/api/v1/recognition-rule-packs/{imported_pack['id']}/export", headers=headers)
        assert exported.status_code == 404


def test_recognition_rule_pack_import_rejects_pack_without_order_row_parser() -> None:
    payload = {
        "contract_version": "recognition_rule_pack_v1",
        "pack": {
            "code": "metadata-only",
            "name": "只有元信息的规则包",
            "version": "1.0.0",
        },
        "parser_policy": {
            "requires_active_rule_pack": True,
        },
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        imported = client.post(
            "/api/v1/recognition-rule-packs/import",
            headers=headers,
            json={"payload": payload, "activate": True},
        )

    assert imported.status_code == 422
    assert "parser_policy.order_row_parser" in imported.json()["detail"]


def test_order_row_draft_endpoint_requires_active_rule_pack() -> None:
    payload = {
        "task": {
            "documents": [
                {
                    "documentID": "DOC-RULE-PACK-MISSING",
                    "contents": [
                        {
                            "data": {
                                "productCount": "1件",
                                "productInfo": "【鞋款A】黑色 42 1件",
                                "productShortInfo": "【鞋款A】黑色 42 1件",
                            }
                        }
                    ],
                }
            ]
        }
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        with SessionLocal() as db:
            db.query(RecognitionRulePack).filter(RecognitionRulePack.workspace_id == 1).update(
                {"is_enabled": False, "status": "inactive"}
            )
            record = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=8799,
                document_id="DOC-RULE-PACK-MISSING",
                source_component="cloud-print-client",
                source_index="1",
                payload_format="json",
                raw_payload=json.dumps(payload, ensure_ascii=False),
                status="pending",
            )
            db.add(record)
            db.commit()

        response = client.get("/api/v1/order-row-drafts/tasks/8799", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rule_pack_missing"
    assert body["rule_pack_required"] is True
    assert body["rows"] == []
    assert body["summary"]["pending_rule_pack_count"] == 1


def test_order_row_draft_endpoint_returns_child_waybill_rows(monkeypatch) -> None:
    use_order_row_parser_service_stub(monkeypatch)

    product_line = (
        "【登山鞋涉水鞋防水机能户外徒步男鞋女鞋休闲防滑溯溪越野鞋跑步鞋】低帮黑白 42 1件；"
        "【登山鞋涉水鞋防水机能户外徒步男鞋女鞋休闲防滑溯溪越野鞋跑步鞋】低帮深卡其 39 1件"
    )
    payload = {
        "task": {
            "documents": [
                {
                    "documentID": "DOC-3",
                    "contents": [
                        {
                            "data": {
                                "productCount": "2件",
                                "productInfo": product_line,
                                "productShortInfo": product_line,
                            }
                        }
                    ],
                }
            ]
        }
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        activate_test_rule_pack()

        with SessionLocal() as db:
            record = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=8801,
                document_id="DOC-3",
                source_component="cloud-print-client",
                source_index="24",
                payload_format="json",
                raw_payload=json.dumps(payload, ensure_ascii=False),
                status="pending",
            )
            db.add(record)
            db.commit()

        response = client.get("/api/v1/order-row-drafts/tasks/8801", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "order_row_drafts_v1"
    assert body["summary"] == {
        "parent_waybill_count": 1,
        "child_waybill_count": 2,
        "draft_count": 2,
        "needs_review_count": 0,
        "special_count": 0,
    }
    assert [row["child_label"] for row in body["rows"]] == [
        "第1批-第1单-子1",
        "第1批-第1单-子2",
    ]
    assert [row["source_index"] for row in body["rows"]] == ["24", "24"]


def test_order_row_draft_endpoint_expands_raw_record_documents_as_waybills(monkeypatch) -> None:
    use_order_row_parser_service_stub(monkeypatch)

    payload = {
        "cmd": "print",
        "task": {
            "documents": [
                {
                    "documentID": "DOC-SAMPLE-1",
                    "contents": [
                        {
                            "printXML": "<text><![CDATA[5.0范48，,5.0二代白黑红，39*1]]></text>",
                        }
                    ],
                },
                {
                    "documentID": "DOC-SAMPLE-2",
                    "contents": [
                        {
                            "printXML": "<text><![CDATA[范33 带木one帆布kw，木村-3M反光，42.5*1]]></text>",
                        }
                    ],
                },
            ]
        },
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        activate_test_rule_pack()

        with SessionLocal() as db:
            record = RawCaptureRecord(
                tenant_id=1,
                workspace_id=1,
                task_id=8804,
                document_id="RAW-MULTI-DOC",
                source_component="cainiao-cnprint",
                source_index="7134",
                payload_format="json",
                raw_payload=json.dumps(payload, ensure_ascii=False),
                status="pending",
            )
            db.add(record)
            db.commit()

        response = client.get("/api/v1/order-row-drafts/tasks/8804", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "parent_waybill_count": 2,
        "child_waybill_count": 2,
        "draft_count": 2,
        "needs_review_count": 0,
        "special_count": 0,
    }
    assert [row["child_label"] for row in body["rows"]] == [
        "第1批-第1单-子1",
        "第1批-第2单-子1",
    ]
    assert [row["source_index"] for row in body["rows"]] == ["7134", "7134"]
    assert [row["product"] for row in body["rows"]] == ["5.0范48", "范33 带木one帆布kw"]
    assert [row["sales_attr1"] for row in body["rows"]] == ["5.0二代白黑红", "木村-3M反光"]
    assert [row["sales_attr2"] for row in body["rows"]] == ["39", "42.5"]
    assert [row["quantity"] for row in body["rows"]] == [1, 1]


def test_order_row_draft_endpoint_prefers_standard_details_as_business_waybills(monkeypatch) -> None:
    use_order_row_parser_service_stub(monkeypatch)

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        activate_test_rule_pack()

        with SessionLocal() as db:
            db.add_all(
                [
                    RawCaptureRecord(
                        tenant_id=1,
                        workspace_id=1,
                        task_id=8802,
                        document_id="RAW-DOC-1",
                        source_component="cloud-print-client",
                        source_index="9000",
                        payload_format="json",
                        raw_payload=json.dumps({"task": {"documents": []}}, ensure_ascii=False),
                        status="parsed",
                    ),
                    StandardDetail(
                        tenant_id=1,
                        workspace_id=1,
                        standard_detail_batch_id=1,
                        waybill_mode="douyin_cloud_print",
                        full_text="【鞋款A】黑色 42 1件",
                        field_values={
                            "capture_task_id": 8802,
                            "raw_record_id": 1,
                            "source_component": "cloud-print-client",
                            "source_index": "9000",
                            "product_short_text": "【鞋款A】黑色 42 1件",
                        },
                    ),
                    StandardDetail(
                        tenant_id=1,
                        workspace_id=1,
                        standard_detail_batch_id=1,
                        waybill_mode="douyin_cloud_print",
                        full_text="【鞋款B】白色 39 1件",
                        field_values={
                            "capture_task_id": 8802,
                            "raw_record_id": 1,
                            "source_component": "cloud-print-client",
                            "source_index": "9000",
                            "product_short_text": "【鞋款B】白色 39 1件",
                        },
                    ),
                ]
            )
            db.commit()

        response = client.get("/api/v1/order-row-drafts/tasks/8802", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["parent_waybill_count"] == 2
    assert body["summary"]["child_waybill_count"] == 2
    assert [row["child_label"] for row in body["rows"]] == [
        "第1批-第1单-子1",
        "第1批-第2单-子1",
    ]
    assert [row["product"] for row in body["rows"]] == ["鞋款A", "鞋款B"]


def test_order_row_draft_endpoint_prefers_current_raw_waybill_samples_over_stale_standard_details(
    monkeypatch,
) -> None:
    use_order_row_parser_service_stub(monkeypatch)

    payload = {
        "task": {
            "documents": [
                {
                    "documentID": "RAW-CURRENT-DOC",
                    "contents": [
                        {
                            "data": {
                                "productCount": "1件",
                                "productInfo": "当前原始面单商品 绿色 42 1件",
                                "productShortInfo": "当前原始面单商品 绿色 42 1件",
                            }
                        }
                    ],
                }
            ]
        }
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        activate_test_rule_pack()

        with SessionLocal() as db:
            db.add(
                RawCaptureRecord(
                    tenant_id=1,
                    workspace_id=1,
                    task_id=8805,
                    document_id="RAW-CURRENT-DOC",
                    source_component="cloud-print-client",
                    source_index="current-raw",
                    payload_format="json",
                    raw_payload=json.dumps(payload, ensure_ascii=False),
                    status="parsed",
                )
            )
            db.add(
                StandardDetail(
                    tenant_id=1,
                    workspace_id=1,
                    standard_detail_batch_id=1,
                    waybill_mode="legacy_cache",
                    full_text="旧缓存商品 黑色 39 1件",
                    field_values={
                        "capture_task_id": 8805,
                        "raw_record_id": 9999,
                        "source_component": "legacy",
                        "source_index": "stale-cache",
                        "product_short_text": "旧缓存商品 黑色 39 1件",
                    },
                )
            )
            db.commit()

        response = client.get("/api/v1/order-row-drafts/tasks/8805", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["parent_waybill_count"] == 1
    assert body["summary"]["child_waybill_count"] == 1
    assert [row["source_index"] for row in body["rows"]] == ["current-raw"]
    assert "当前原始面单商品" in body["rows"][0]["product"]
    assert "旧缓存商品" not in json.dumps(body, ensure_ascii=False)


def test_order_row_draft_endpoint_requires_parser_service_when_rule_pack_active(monkeypatch) -> None:
    monkeypatch.setattr(order_row_reader_service, "waybill_parser_service_enabled", lambda: False, raising=False)

    payload = {
        "task": {
            "documents": [
                {
                    "documentID": "DOC-SERVICE-REQUIRED",
                    "contents": [
                        {
                            "data": {
                                "productCount": "1件",
                                "productInfo": "【不允许本地解析】黑色 42 1件",
                                "productShortInfo": "【不允许本地解析】黑色 42 1件",
                            }
                        }
                    ],
                }
            ]
        }
    }

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        activate_test_rule_pack()

        with SessionLocal() as db:
            db.add(
                RawCaptureRecord(
                    tenant_id=1,
                    workspace_id=1,
                    task_id=8805,
                    document_id="DOC-SERVICE-REQUIRED",
                    source_component="cloud-print-client",
                    source_index="service-required",
                    payload_format="json",
                    raw_payload=json.dumps(payload, ensure_ascii=False),
                    status="pending",
                )
            )
            db.commit()

        response = client.get("/api/v1/order-row-drafts/tasks/8805", headers=headers)

    assert response.status_code == 503
    assert "旧解析兜底" in response.json()["detail"]


def test_order_row_draft_endpoint_uses_configured_parser_service(monkeypatch) -> None:
    def fake_parse_order_row_drafts_with_service(
        *,
        task_id: int,
        standard_details: list[dict],
        raw_records: list[dict],
        rule_pack: dict,
    ) -> dict:
        assert task_id == 8803
        assert len(standard_details) == 1
        assert raw_records == []
        assert rule_pack["pack"]["code"] == "test-shoes"
        return {
            "contract_version": "order_row_drafts_v1",
            "task_id": task_id,
            "summary": {
                "parent_waybill_count": 1,
                "child_waybill_count": 1,
                "draft_count": 1,
                "needs_review_count": 0,
                "special_count": 0,
            },
            "parents": [
                {
                    "raw_record_id": 10,
                    "task_id": task_id,
                    "parent_label": "远端解析-父单",
                    "source_component": "parser-test",
                    "source_index": "remote",
                    "child_count": 1,
                    "rows": [],
                }
            ],
            "rows": [
                {
                    "raw_record_id": 10,
                    "task_id": task_id,
                    "parent_label": "远端解析-父单",
                    "child_label": "远端解析-子单",
                    "child_index": 1,
                    "child_count": 1,
                    "source_component": "parser-test",
                    "source_index": "remote",
                    "product": "远端鞋款",
                    "sales_attr1": "黑色",
                    "sales_attr2": "42",
                    "quantity": 1,
                    "remark": "",
                    "image_match_text": "远端鞋款 黑色 42 1",
                    "original_text": "远端鞋款，黑色，42",
                    "status": "draft",
                    "review_reason": "",
                }
            ],
        }

    monkeypatch.setattr(order_row_reader_service, "waybill_parser_service_enabled", lambda: True, raising=False)
    monkeypatch.setattr(
        order_row_reader_service,
        "parse_order_row_drafts_with_service",
        fake_parse_order_row_drafts_with_service,
        raising=False,
    )

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        activate_test_rule_pack()

        with SessionLocal() as db:
            db.add(
                StandardDetail(
                    tenant_id=1,
                    workspace_id=1,
                    standard_detail_batch_id=1,
                    waybill_mode="douyin_cloud_print",
                    full_text="【本地不应被调用】黑色 42 1件",
                    field_values={
                        "capture_task_id": 8803,
                        "raw_record_id": 10,
                        "source_component": "cloud-print-client",
                        "source_index": "10",
                        "product_short_text": "【本地不应被调用】黑色 42 1件",
                    },
                )
            )
            db.commit()

        response = client.get("/api/v1/order-row-drafts/tasks/8803", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["parents"][0]["parent_label"] == "远端解析-父单"
    assert body["rows"][0]["product"] == "远端鞋款"


def test_order_row_draft_endpoint_does_not_fallback_when_parser_service_fails(monkeypatch) -> None:
    def broken_parse_order_row_drafts_with_service(**_kwargs: dict) -> dict:
        raise RuntimeError("parser unavailable")

    monkeypatch.setattr(order_row_reader_service, "waybill_parser_service_enabled", lambda: True, raising=False)
    monkeypatch.setattr(
        order_row_reader_service,
        "parse_order_row_drafts_with_service",
        broken_parse_order_row_drafts_with_service,
        raising=False,
    )

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Workspace-Id": "1"}

        activate_test_rule_pack()

        with SessionLocal() as db:
            db.add(
                StandardDetail(
                    tenant_id=1,
                    workspace_id=1,
                    standard_detail_batch_id=1,
                    waybill_mode="douyin_cloud_print",
                    full_text="【旧解析不应兜底】黑色 42 1件",
                    field_values={
                        "capture_task_id": 8804,
                        "raw_record_id": 11,
                        "source_component": "cloud-print-client",
                        "source_index": "11",
                        "product_short_text": "【旧解析不应兜底】黑色 42 1件",
                    },
                )
            )
            db.commit()

        response = client.get("/api/v1/order-row-drafts/tasks/8804", headers=headers)

    assert response.status_code == 502
    assert "面单解析服务暂时不可用" in response.json()["detail"]
