from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any
import unicodedata
from xml.etree import ElementTree


def _field(key: str, label: str, path: str, *, selected: bool = True) -> dict[str, Any]:
    return {"key": key, "label": label, "path": path, "default_selected": selected}


FINGERPRINT_CATALOG = (
    {
        "code": "CN-ITEM-INFO",
        "name": "菜鸟商品文本型",
        "description": "从菜鸟商品信息字段读取商品文字。",
        "detect_path": "contents[].data.ITEM_INFO",
        "fields": (
            _field("item_info", "商品信息", "contents[].data.ITEM_INFO"),
            _field("seller_memo", "卖家备注", "contents[].data.SELLER_MEMO"),
            _field("buyer_memo", "买家备注", "contents[].data.BUYER_MEMO", selected=False),
            _field("item_total_count", "商品总数量", "contents[].data.ITEM_TOTAL_COUNT"),
        ),
    },
    {
        "code": "CN-PRINT-XML",
        "name": "菜鸟打印 XML 型",
        "description": "把菜鸟打印 XML 转成纯文本后提供给 AI。",
        "detect_path": "contents[].printXML",
        "fields": (_field("print_text", "打印文本", "contents[].printXML//text"),),
    },
    {
        "code": "CN-CUSTOM-CONTENT",
        "name": "菜鸟自定义内容型",
        "description": "读取菜鸟自定义商品内容。",
        "detect_path": "contents[].data.customContent",
        "fields": (_field("custom_content", "自定义商品内容", "contents[].data.customContent"),),
    },
    {
        "code": "CN-PACKAGE-ITEMS",
        "name": "菜鸟包裹明细型",
        "description": "读取菜鸟包裹中的商品和规格明细。",
        "detect_path": "contents[].data.packageItemDetail[]",
        "fields": (
            _field("item_name", "商品名称", "packageItemDetail[].itemName"),
            _field("simple_name", "商品简称", "packageItemDetail[].simpleName", selected=False),
            _field("sku_full_name", "完整 SKU", "packageItemDetail[].skuFullName"),
            _field("spec_name", "规格名称", "packageItemDetail[].specName"),
            _field("spec_simple_name", "规格简称", "packageItemDetail[].specSimpleName", selected=False),
            _field("sku_size", "尺码", "packageItemDetail[].skuSize"),
            _field("item_quantity", "商品数量", "packageItemDetail[].itemNum"),
        ),
    },
    {
        "code": "CLOUD-PRODUCT-INFO",
        "name": "云打印商品信息型",
        "description": "读取云打印返回的商品、规格和数量字段。",
        "detect_path": "contents[].data.productInfo",
        "fields": (
            _field("product_info", "商品信息", "contents[].data.productInfo"),
            _field("product_short_info", "商品简要信息", "contents[].data.productShortInfo"),
            _field("spec_info", "规格信息", "contents[].data.sPInfo"),
            _field("spec_short_info", "规格简要信息", "contents[].data.sPSInfo", selected=False),
            _field("remark", "商家备注", "contents[].data.remark"),
            _field("buyer_remark", "买家备注", "contents[].data.buyerRemark", selected=False),
            _field("product_count", "商品数量", "contents[].data.productCount"),
        ),
    },
)

FIELD_SOURCE_KEYS = {
    "item_info": "ITEM_INFO",
    "seller_memo": "SELLER_MEMO",
    "buyer_memo": "BUYER_MEMO",
    "item_total_count": "ITEM_TOTAL_COUNT",
    "custom_content": "customContent",
    "item_name": "itemName",
    "simple_name": "simpleName",
    "sku_full_name": "skuFullName",
    "spec_name": "specName",
    "spec_simple_name": "specSimpleName",
    "sku_size": "skuSize",
    "item_quantity": "itemNum",
    "product_info": "productInfo",
    "product_short_info": "productShortInfo",
    "spec_info": "sPInfo",
    "spec_short_info": "sPSInfo",
    "remark": "remark",
    "buyer_remark": "buyerRemark",
    "product_count": "productCount",
}


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_dicts(item)


def _data_nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [node["data"] for node in _walk_dicts(payload) if isinstance(node.get("data"), dict)]


def _values(nodes: list[dict[str, Any]], key: str) -> list[Any]:
    return [node[key] for node in nodes if key in node and node[key] not in ("", None, [])]


def _field_value(values: list[Any]) -> Any:
    return "" if not values else values[0] if len(values) == 1 else values


def _print_text_blocks(payload: dict[str, Any]) -> list[str]:
    blocks: list[str] = []
    for node in _walk_dicts(payload):
        xml = node.get("printXML")
        if not isinstance(xml, str) or not xml.strip():
            continue
        try:
            root = ElementTree.fromstring(xml)
            blocks.extend(
                text
                for element in root.iter()
                if element.tag.rsplit("}", 1)[-1] == "text"
                for text in [" ".join("".join(element.itertext()).split())]
                if text
            )
        except ElementTree.ParseError:
            blocks.extend(" ".join(text.split()) for text in re.findall(r"<!\[CDATA\[(.*?)\]\]>", xml, re.S) if text.strip())
    return blocks


def _print_text(payload: dict[str, Any]) -> str:
    return "\n".join(dict.fromkeys(_print_text_blocks(payload)))


def _item_nodes(data_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for data in data_nodes for item in data.get("packageItemDetail", []) if isinstance(item, dict)]


def _definition(code: str) -> dict[str, Any]:
    return next(item for item in FINGERPRINT_CATALOG if item["code"] == code)


def _detected(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    data_nodes = _data_nodes(payload)
    item_nodes = _item_nodes(data_nodes)
    print_blocks = _print_text_blocks(payload)
    if _values(data_nodes, "ITEM_INFO"):
        return "CN-ITEM-INFO", data_nodes, item_nodes, print_blocks
    if print_blocks:
        return "CN-PRINT-XML", data_nodes, item_nodes, print_blocks
    if _values(data_nodes, "customContent"):
        return "CN-CUSTOM-CONTENT", data_nodes, item_nodes, print_blocks
    if item_nodes:
        return "CN-PACKAGE-ITEMS", data_nodes, item_nodes, print_blocks
    if _values(data_nodes, "productInfo"):
        return "CLOUD-PRODUCT-INFO", data_nodes, item_nodes, print_blocks
    return "UNKNOWN", data_nodes, item_nodes, print_blocks


def fingerprint_catalog() -> list[dict[str, Any]]:
    return [
        {
            "code": item["code"],
            "name": item["name"],
            "description": item["description"],
            "detect_path": item["detect_path"],
            "fields": [dict(field) for field in item["fields"]],
        }
        for item in FINGERPRINT_CATALOG
    ]


def inspect_fingerprint(payload: dict[str, Any], source_component: str = "") -> dict[str, Any] | None:
    del source_component
    code, data_nodes, item_nodes, print_blocks = _detected(payload)
    if code == "UNKNOWN":
        return None
    definition = _definition(code)
    source_nodes = item_nodes if code == "CN-PACKAGE-ITEMS" else data_nodes
    print_text = "\n".join(dict.fromkeys(print_blocks))
    fields = [
        {
            **field,
            "value": print_text if field["key"] == "print_text" else _field_value(_values(source_nodes, FIELD_SOURCE_KEYS[field["key"]])),
        }
        for field in definition["fields"]
    ]
    return {"fingerprint_code": code, "fingerprint_name": definition["name"], "fields": fields}


def _structure_signature(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _structure_signature(item) for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))}
    if isinstance(value, list):
        signatures: list[Any] = []
        seen: set[str] = set()
        for item in value[:10]:
            signature = _structure_signature(item)
            encoded = json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if encoded not in seen:
                seen.add(encoded)
                signatures.append(signature)
        return {"type": "list", "items": signatures}
    if isinstance(value, str):
        return {"type": "scalar"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, (bool, int, float)):
        return {"type": "scalar"}
    return {"type": type(value).__name__}


def legacy_structural_fingerprint(payload: dict[str, Any], source_component: str) -> str:
    encoded = json.dumps(
        {"source_component": source_component.strip().lower(), "payload": _structure_signature(payload)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _scalar_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, str):
        return "str"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return type(value).__name__


def _text_grammar(text: str) -> str:
    return re.sub(r"\s+", " ", "".join(char for char in text if char.isspace() or unicodedata.category(char).startswith("P"))).strip()


def _business_signature(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    code, data_nodes, item_nodes, print_blocks = _detected(payload)
    if code == "CN-PRINT-XML":
        return code, {"text": [_text_grammar(block) for block in print_blocks]}
    if code == "CN-PACKAGE-ITEMS":
        fields = {FIELD_SOURCE_KEYS[field["key"]] for field in _definition(code)["fields"]}
        layouts = {
            tuple(sorted((key, _scalar_type(item[key])) for key in fields if key in item))
            for item in item_nodes
        }
        return code, {"fields": sorted(layouts)}
    if code != "UNKNOWN":
        fields = {FIELD_SOURCE_KEYS[field["key"]] for field in _definition(code)["fields"]}
        layouts = {
            tuple(sorted((key, _scalar_type(node[key])) for key in fields if key in node))
            for node in data_nodes
        }
        return code, {"fields": sorted(layouts)}
    return code, _structure_signature(payload)


def business_shape_fingerprint(payload: dict[str, Any], source_component: str) -> str:
    code, shape = _business_signature(payload)
    encoded = json.dumps(
        {"source_component": source_component.strip().lower(), "fingerprint_code": code, "shape": shape},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"v2:{code}:sha256:{sha256(encoded).hexdigest()}"


def fingerprint_for_payload(payload: dict[str, Any], source_component: str, strategy: str) -> str:
    if strategy == "legacy_structure_v1":
        return legacy_structural_fingerprint(payload, source_component)
    if strategy == "business_shape_v2":
        return business_shape_fingerprint(payload, source_component)
    raise ValueError(f"unsupported fingerprint strategy: {strategy}")
