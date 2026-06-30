from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import quote
from typing import Annotated, Any
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from openpyxl import Workbook
from openpyxl.drawing.image import Image as WorksheetImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU
from PIL import Image as PillowImage, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.context import CurrentUser
from app.core.database import get_db
from app.core.deps import get_current_user, get_workspace_id, require_write
from app.core.security import create_collector_token, hash_collector_token
from app.models import (
    CaptureTask,
    Collector,
    ExportHeaderDefinition,
    ImageAsset,
    Product,
    ProductSku,
    RawCaptureRecord,
    StandardDetail,
    StandardDetailBatch,
    Workspace,
)
from app.repositories.base import model_to_dict
from app.api.routes.product_sku_linking import (
    ProductMatchingScope,
    ProductSkuLinkingPreviewRequest,
    preview_with_rules as preview_product_sku_with_rules,
    rows_for_preview as product_sku_rows_for_preview,
    saved_rule_payloads as saved_product_sku_rule_payloads,
)
from app.services.collection_contract import (
    build_raw_capture_record,
)
from app.services.product_sku_linking import exportable_product_sku_linking_result
from app.services.recognition_rule_packs import (
    RULE_PACK_MISSING_STATUS,
    active_recognition_rule_pack,
)
from app.services.waybill_reading import read_waybill_samples


router = APIRouter()

COLLECTOR_HEARTBEAT_TIMEOUT = timedelta(seconds=60)
COLLECTOR_CLEANUP_TIMEOUT = timedelta(hours=24)

COLLECTOR_CLIENT_ARCHIVE_ROOT = "Cargo Platform 采集器"
COLLECTOR_CLIENT_RELEASE_EXE = Path("dist") / "Cargo Platform 采集器.exe"
COLLECTOR_CLIENT_PACKAGE_VERSION = "single-exe-token-collector-20260614"
RAW_CAPTURE_BATCH_MAX_RECORDS = 100
RAW_CAPTURE_PAYLOAD_MAX_CHARS = 2_000_000
RAW_CAPTURE_SOURCE_COLUMNS_MAX_CHARS = 20_000
BUSINESS_DOWNLOAD_TIMEZONE = timezone(timedelta(hours=8))
BUSINESS_REPORT_DOWNLOAD_PREFIX = "订单整理文档"
COLLECTOR_PENDING_MACHINE_NAME = "等待业务机上报机器名"
DEFAULT_COLLECTOR_DISPLAY_NAMES = {
    "",
    "Cargo Platform 采集器",
    "业务机采集器",
    "本机采集器",
    "采集器",
    COLLECTOR_PENDING_MACHINE_NAME,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_optional_text(value: Any) -> str:
    return str(value or "").strip()


def is_default_collector_display_name(value: Any) -> bool:
    return clean_optional_text(value) in DEFAULT_COLLECTOR_DISPLAY_NAMES


def collector_display_name(
    value: Any,
    *,
    source_machine: Any = None,
    collector_id: Any = None,
) -> str:
    name = clean_optional_text(value)
    if not is_default_collector_display_name(name):
        return name
    machine = clean_optional_text(source_machine)
    if machine:
        return machine
    identity = clean_optional_text(collector_id)
    if identity and not identity.startswith("collector-"):
        return identity
    return COLLECTOR_PENDING_MACHINE_NAME


def parse_utc_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def collector_heartbeat_is_stale(collector: Collector) -> bool:
    if collector.online_status != "online":
        return False
    last_heartbeat_at = parse_utc_datetime(collector.last_heartbeat_at)
    if last_heartbeat_at is None:
        return True
    return datetime.now(timezone.utc) - last_heartbeat_at > COLLECTOR_HEARTBEAT_TIMEOUT


def collector_should_be_cleaned(collector: Collector) -> bool:
    if collector.online_status != "online":
        return False
    last_heartbeat_at = parse_utc_datetime(collector.last_heartbeat_at)
    if last_heartbeat_at is None:
        return True
    return datetime.now(timezone.utc) - last_heartbeat_at > COLLECTOR_CLEANUP_TIMEOUT


def cleanup_collector(collector: Collector, *, user_id: int | None = None) -> None:
    status_payload = collector.status_payload if isinstance(collector.status_payload, dict) else {}
    collector.status_payload = {
        **status_payload,
        "runtime_status": "cleaned",
        "stale_reason": "heartbeat_cleanup",
        "heartbeat_cleanup_hours": int(COLLECTOR_CLEANUP_TIMEOUT.total_seconds() // 3600),
        "cleaned_at": utc_now(),
    }
    collector.online_status = "offline"
    collector.is_enabled = False
    collector.token_hash = None
    collector.is_deleted = True
    collector.updated_by = user_id


def cleanup_expired_collectors(
    db: Session,
    *,
    workspace_id: int | None = None,
    user_id: int | None = None,
) -> int:
    statement = select(Collector).where(
        Collector.is_deleted.is_(False),
        Collector.is_enabled.is_(True),
        Collector.online_status == "online",
    )
    if workspace_id is not None:
        statement = statement.where(Collector.workspace_id == workspace_id)

    cleaned_count = 0
    for collector in db.scalars(statement).all():
        if collector_should_be_cleaned(collector):
            cleanup_collector(collector, user_id=user_id)
            cleaned_count += 1
    if cleaned_count:
        db.commit()
    return cleaned_count


def get_workspace_tenant_id(db: Session, workspace_id: int) -> int | None:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None or workspace.is_deleted:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace access denied.")
    return workspace.tenant_id


def collector_client_source_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "collector-client"


def collector_client_archive_path(name: str) -> str:
    return str(Path(COLLECTOR_CLIENT_ARCHIVE_ROOT) / name)


def write_collector_client_version(zip_file: ZipFile, *, mode: str) -> None:
    zip_file.writestr(
        collector_client_archive_path("VERSION.txt"),
        (
            f"version={COLLECTOR_CLIENT_PACKAGE_VERSION}\n"
            f"mode={mode}\n"
            "package=single-exe-token-collector\n"
            "features=single-exe,no-console-window,token-only,no-password-on-business-machine,server-reconnect-wait,remote-disconnect-guard\n"
        ),
    )


def collector_client_parameter_guide() -> str:
    return (
        "Cargo Platform 采集器参数说明\n"
        "\n"
        "文件：Cargo Platform 采集器.exe\n"
        "\n"
        "复制前只需要替换两处：\n"
        "1. <TOKEN> 换成网页后台生成的采集器 token。\n"
        "2. <服务器地址> 换成系统访问地址，例如 http://服务器IP:5173；不要填写 8000 端口。\n"
        "\n"
        "常用启动方式：\n"
        "\n"
        "1. 正式后台监听（最常用）\n"
        "\"Cargo Platform 采集器.exe\" --base-url \"<服务器地址>\" --token \"<TOKEN>\" --collector-name \"%COMPUTERNAME%\" --loop\n"
        "\n"
        "2. 指定后台显示名称（正常 CMD 中建议直接使用本机机器名）\n"
        "\"Cargo Platform 采集器.exe\" --base-url \"<服务器地址>\" --token \"<TOKEN>\" --collector-name \"%COMPUTERNAME%\" --loop\n"
        "\n"
        "3. 先保存配置，再后台启动（后续启动命令最短）\n"
        "\"Cargo Platform 采集器.exe\" --base-url \"<服务器地址>\" --token \"<TOKEN>\" --collector-name \"%COMPUTERNAME%\" --save-config\n"
        "\"Cargo Platform 采集器.exe\" --loop\n"
        "\n"
        "4. 指定日志文件位置\n"
        "\"Cargo Platform 采集器.exe\" --base-url \"<服务器地址>\" --token \"<TOKEN>\" --collector-name \"%COMPUTERNAME%\" --loop --log-file \"%LOCALAPPDATA%\\CargoPlatformCollector\\collector.log\"\n"
        "\n"
        "5. 只检查连接和本机打印组件，不持续监听\n"
        "\"Cargo Platform 采集器.exe\" --base-url \"<服务器地址>\" --token \"<TOKEN>\" --collector-name \"%COMPUTERNAME%\" --check --log-file \"%LOCALAPPDATA%\\CargoPlatformCollector\\collector-check.log\"\n"
        "\n"
        "常用参数：\n"
        "--base-url        系统访问地址；不要填写 8000 端口。例如 http://服务器IP:5173。\n"
        "--token           后台生成的采集器 token，必填。业务机不再输入系统账号密码。\n"
        "--loop            持续后台监听；服务器断开或重启时不会退出，会继续等待恢复。\n"
        "--collector-name  后台显示名称；留空或使用旧默认名时，系统会自动改成本机 Windows 机器名。\n"
        "--interval        心跳和采集轮询间隔，默认 3 秒。\n"
        "--config          可选配置文件路径，默认保存在当前 Windows 用户的 LocalAppData。\n"
        "--state           可选状态文件路径，默认和配置文件同目录。\n"
        "--log-file        可选日志文件路径，默认和配置文件同目录 collector.log。\n"
        "--save-config     保存当前 base-url/token/名称等配置后退出；以后可直接用 \"Cargo Platform 采集器.exe\" --loop。\n"
        "--check           检查本机打印组件和服务器心跳后退出；不进入持续监听。\n"
        "\n"
        "设备标识说明：\n"
        "用户不需要填写设备标识。采集器会自动读取业务机 Windows 机器名作为设备标识并上传。\n"
        "\n"
        "后台运行说明：\n"
        "这个 exe 是无控制台窗口版本，按参数启动后不会弹黑框。日志默认写入：\n"
        "%LOCALAPPDATA%\\CargoPlatformCollector\\collector.log\n"
        "\n"
        "不要使用的旧方式：\n"
        "不要在业务机运行 Python，不要用 bat/vbs，不要输入系统账号密码，不要填写后端 8000 端口。\n"
        "\n"
        "token 失效时：\n"
        "在系统后台移除旧采集器并重新生成 token，再用同一条启动命令替换 token。不要在业务机保存或输入系统登录密码。\n"
    )


def write_collector_client_release(zip_file: ZipFile, source_dir: Path, *, mode: str) -> None:
    exe_path = source_dir / COLLECTOR_CLIENT_RELEASE_EXE
    if not exe_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cargo Platform 采集器.exe not found. Build the collector client first.",
        )
    write_collector_client_version(zip_file, mode=mode)
    zip_file.write(exe_path, collector_client_archive_path("Cargo Platform 采集器.exe"))
    zip_file.writestr(
        collector_client_archive_path("参数说明.txt"),
        collector_client_parameter_guide(),
    )


def build_collector_client_archive(mode: str = "cli") -> BytesIO:
    source_dir = collector_client_source_dir()
    if not source_dir.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collector client package not found.")

    archive = BytesIO()
    with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
        write_collector_client_release(zip_file, source_dir, mode=mode)
    archive.seek(0)
    return archive


def collector_client_release_status() -> dict[str, Any]:
    source_dir = collector_client_source_dir()
    exe_path = source_dir / COLLECTOR_CLIENT_RELEASE_EXE
    release_available = exe_path.is_file()
    return {
        "package_version": COLLECTOR_CLIENT_PACKAGE_VERSION,
        "release_available": release_available,
        "status": "ready" if release_available else "missing",
        "archive_name": "订单整理系统采集器.zip",
        "release_exe": str(COLLECTOR_CLIENT_RELEASE_EXE).replace("\\", "/"),
        "message": (
            "采集器发布包已就绪。"
            if release_available
            else "采集器 exe 发布包缺失，需要先构建 collector-client/dist/Cargo Platform 采集器.exe。"
        ),
    }


class CollectorRegisterRequest(BaseModel):
    collector_id: str | None = Field(default=None, max_length=128)
    collector_name: str = Field(default="", max_length=128)
    source_machine: str | None = Field(default=None, max_length=128)
    client_version: str | None = Field(default=None, max_length=64)
    remark: str | None = None


class CaptureStartRequest(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    collector_id: int | None = None


class CaptureStopRequest(BaseModel):
    task_id: int | None = None


class CollectorHeartbeatRequest(BaseModel):
    collector_id: str | None = Field(default=None, max_length=128)
    collector_name: str | None = Field(default=None, max_length=128)
    source_machine: str | None = Field(default=None, max_length=128)
    client_version: str | None = Field(default=None, max_length=64)
    runtime_status: str | None = Field(default=None, max_length=32)
    adapter_status: dict[str, Any] | None = None
    queue_size: int | None = None
    last_error: str | None = None


class RawCaptureRecordPayload(BaseModel):
    """Collector upload payload whose public persisted output is raw_capture_record."""

    document_id: str | None = Field(default=None, max_length=128)
    source_machine: str | None = Field(default=None, max_length=128)
    source_component: str | None = Field(default=None, max_length=128)
    source_index: str | None = Field(default=None, max_length=128)
    dedupe_key: str | None = Field(default=None, max_length=255)
    waybill_mode: str | None = Field(default=None, max_length=128)
    payload_format: str = Field(default="unknown", max_length=32)
    raw_payload: str = Field(min_length=1, max_length=RAW_CAPTURE_PAYLOAD_MAX_CHARS)
    source_columns: dict[str, Any] | None = None
    parsed_payload: dict[str, Any] | None = None
    captured_at: str | None = Field(default=None, max_length=64)

    @field_validator("source_columns")
    @classmethod
    def source_columns_must_be_audit_sized(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(serialized) > RAW_CAPTURE_SOURCE_COLUMNS_MAX_CHARS:
            raise ValueError(
                f"source_columns must be at most {RAW_CAPTURE_SOURCE_COLUMNS_MAX_CHARS} JSON characters."
            )
        return value


class RawCaptureBatchRequest(BaseModel):
    task_id: int
    records: list[RawCaptureRecordPayload] = Field(
        min_length=1,
        max_length=RAW_CAPTURE_BATCH_MAX_RECORDS,
    )


class ParseRecordsRequest(BaseModel):
    task_id: int | None = None
    force: bool = False


class ArchiveCaptureDataRequest(BaseModel):
    days_before: int | None = Field(default=None, ge=0, le=3650)


class DeleteArchivedCaptureDataRequest(BaseModel):
    confirm_text: str
    days_before: int | None = Field(default=None, ge=0, le=3650)


def public_collector(collector: Collector) -> dict[str, Any]:
    data = model_to_dict(collector)
    if not collector_heartbeat_is_stale(collector):
        return data

    status_payload = data.get("status_payload")
    if isinstance(status_payload, str):
        try:
            status_payload = json.loads(status_payload)
        except json.JSONDecodeError:
            status_payload = {}
    elif isinstance(status_payload, dict):
        status_payload = dict(status_payload)
    else:
        status_payload = {}

    status_payload["runtime_status"] = "stale"
    status_payload["stale_reason"] = "heartbeat_timeout"
    status_payload["heartbeat_timeout_seconds"] = int(COLLECTOR_HEARTBEAT_TIMEOUT.total_seconds())
    data["online_status"] = "offline"
    data["status_payload"] = status_payload
    return data


def public_task(task: CaptureTask) -> dict[str, Any]:
    return model_to_dict(task)


def json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def text_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def int_value(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def recognition_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "total": 0,
        "matched": 0,
        "product_unmatched": 0,
        "sku_unmatched": 0,
        "conflict": 0,
    }
    for row in rows:
        summary["total"] += 1
        status_text = text_value(row.get("status"))
        if status_text in summary:
            summary[status_text] += 1
    return summary


def source_component_label(component: Any) -> str:
    component_text = text_value(component)
    if component_text == "cloud-print-client":
        return "抖店打印组件"
    if component_text == "cainiao-cnprint":
        return "菜鸟打印组件"
    return component_text or "-"


def raw_record_collector_label(record: RawCaptureRecord, collectors_by_id: dict[int, Collector]) -> str:
    collector = collectors_by_id.get(int(record.collector_id or 0))
    if collector is not None and text_value(collector.collector_name):
        return text_value(collector.collector_name)
    return text_value(record.source_machine) or "-"


def infer_size_text(*values: Any) -> str:
    text = "\n".join(text_value(value) for value in values if text_value(value))
    labeled = re.search(r"(?:鞋码|尺码|码数|尺碼)\s*[:：]?\s*([2-4]\d(?:\.5)?|50|[XSML]{1,4})", text, re.I)
    if labeled:
        return labeled.group(1)
    generic = re.search(r"(?<!\d)([2-4]\d(?:\.5)?|50)(?!\d)", text)
    return generic.group(1) if generic else ""


CUSTOM_FIELD_FALLBACKS = {
    "custom_spec_text": "custom_sales_attr1_text",
    "custom_size_text": "custom_sales_attr2_text",
    "quantity": "custom_quantity_text",
}


def custom_item_export_values(
    base_values: dict[str, Any],
    item: dict[str, Any],
    *,
    item_index: int,
    item_count: int,
) -> dict[str, Any]:
    values = dict(base_values)
    remark_text = text_value(item.get("remark_text"))
    sales_attr1 = text_value(item.get("sales_attr1_text") or item.get("spec_text"))
    sales_attr2 = text_value(item.get("sales_attr2_text") or item.get("size_text"))
    quantity_text = text_value(item.get("quantity_text"))
    values.update(
        {
            "custom_item_index": item_index,
            "custom_item_count": item_count,
            "custom_item_key": f"{base_values.get('raw_record_id') or base_values.get('raw_document_id')}-{item_index}",
            "custom_product_text": text_value(item.get("product_text")),
            "custom_sales_attr1_text": sales_attr1,
            "custom_sales_attr2_text": sales_attr2,
            "custom_spec_text": text_value(item.get("spec_text")) or sales_attr1,
            "custom_size_text": sales_attr2,
            "custom_quantity_text": quantity_text,
            "custom_item_remark_text": remark_text,
            "custom_item_raw_text": text_value(item.get("raw_text")),
        }
    )
    if quantity_text:
        values["quantity"] = quantity_text
    elif item_count > 1:
        values["quantity"] = ""
    return values


def standard_detail_export_rows(detail: StandardDetail) -> list[dict[str, Any]]:
    values = detail.field_values or {}
    custom_items = values.get("custom_items")
    if not isinstance(custom_items, list) or not custom_items:
        return [values]

    item_dicts = [item for item in custom_items if isinstance(item, dict)]
    if not item_dicts:
        return [values]

    item_count = len(item_dicts)
    return [
        custom_item_export_values(values, item, item_index=index, item_count=item_count)
        for index, item in enumerate(item_dicts, start=1)
    ]


def export_field_value(field_code: str, values: dict[str, Any]) -> Any:
    if field_code == "inferred_size":
        return infer_size_text(
            values.get("custom_sales_attr2_text"),
            values.get("custom_size_text"),
            values.get("custom_item_remark_text"),
            values.get("spec_text"),
            values.get("product_short_text"),
            values.get("product_full_text"),
            values.get("custom_area_raw_text"),
        )
    if field_code == "product_display_text":
        is_woda_custom_row = values.get("source_platform") == "woda" or values.get("custom_area_raw_text") not in (None, "")
        if not is_woda_custom_row:
            return (
                values.get("product_short_text")
                or values.get("product_full_text")
                or values.get("custom_item_raw_text")
                or values.get("custom_product_text")
                or values.get("custom_area_raw_text")
                or ""
            )
        return (
            values.get("custom_product_text")
            or values.get("product_short_text")
            or values.get("product_full_text")
            or values.get("custom_area_raw_text")
            or ""
        )
    value = values.get(field_code)
    if value in (None, "") and field_code in CUSTOM_FIELD_FALLBACKS:
        return values.get(CUSTOM_FIELD_FALLBACKS[field_code], "")
    return value


RECOGNITION_REPORT_HEADERS = ["商品", "销售属性1", "图片", "销售属性2", "数量", "备注", "图片匹配文本"]

RECOGNITION_REPORT_FIELD_DEFINITIONS: dict[str, dict[str, Any]] = {
    "product_name": {"label": "商品", "width": 16},
    "stall_name": {"label": "档口", "width": 14},
    "sales_attr1": {"label": "销售属性1", "width": 24},
    "sku_image": {"label": "图片", "width": 18},
    "sales_attr2": {"label": "销售属性2", "width": 18},
    "quantity": {"label": "数量", "width": 12},
    "remark": {"label": "备注", "width": 18},
    "image_match_text": {"label": "图片匹配文本", "width": 42},
}

RECOGNITION_REPORT_DEFAULT_FIELD_ORDER = [
    "product_name",
    "sales_attr1",
    "sku_image",
    "sales_attr2",
    "quantity",
    "remark",
    "image_match_text",
]

RECOGNITION_REPORT_OUTPUT_MODES = {"merged_sheet", "stall_sheet", "stall_workbooks"}
DEFAULT_RECOGNITION_REPORT_OUTPUT_MODE = "stall_sheet"

RECOGNITION_EXCEPTION_HEADERS = ["图片匹配文本"]
RECOGNITION_EXCEPTION_SHEET_TITLE = "异常面单"
EXPORT_PRODUCT_SKU_LINKING_CONTRACT = "product-sku-linking-results-v1"
EXPORT_PRODUCT_SKU_LINKING_RESULTS_KEY = "product_sku_linking_results"
EXPORT_PRODUCT_SKU_LINKING_RESULT_KEY = "product_sku_linking_result"
EXPORT_PRODUCT_SKU_LINKING_PENDING_STATUS = "pending"

RECOGNITION_REPORT_LEGACY_LABELS = {
    "product_name": {"商品名称"},
    "sku_image": {"SKU图片"},
}

REPORT_IMAGE_SIZE = 88
REPORT_ROW_HEIGHT = 86
REPORT_HEADER_ROW_HEIGHT = 26
REPORT_COLUMN_WIDTH_PIXEL_RATIO = 9
EXCEL_COLUMN_PIXEL_PADDING = 5
EXCEL_COLUMN_UNIT_PIXELS = 7
EXCEL_POINTS_PER_PIXEL = 0.75


def bounded_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return min(max(parsed, min_value), max_value)


def report_layout_width_to_excel_width(value: Any) -> float:
    layout_width = bounded_int(value, 12, 8, 60)
    preview_pixels = layout_width * REPORT_COLUMN_WIDTH_PIXEL_RATIO
    return round(max(8, (preview_pixels - EXCEL_COLUMN_PIXEL_PADDING) / EXCEL_COLUMN_UNIT_PIXELS), 2)


def report_layout_height_to_excel_points(value: Any) -> float:
    height_pixels = bounded_int(value, REPORT_ROW_HEIGHT, 18, 220)
    return round(height_pixels * EXCEL_POINTS_PER_PIXEL, 2)


def default_recognition_report_layout() -> dict[str, Any]:
    return {
        "columns": [
            {
                "key": key,
                "label": RECOGNITION_REPORT_FIELD_DEFINITIONS[key]["label"],
                "visible": True,
                "width": RECOGNITION_REPORT_FIELD_DEFINITIONS[key]["width"],
            }
            for key in RECOGNITION_REPORT_DEFAULT_FIELD_ORDER
        ],
        "header_row_height": REPORT_HEADER_ROW_HEIGHT,
        "row_height": REPORT_ROW_HEIGHT,
        "image_width": REPORT_IMAGE_SIZE,
        "image_height": REPORT_IMAGE_SIZE,
        "image_offset_x": 0,
        "image_offset_y": 0,
        "stack_sales_attr1": False,
        "stack_sales_attr2": False,
        "output_mode": DEFAULT_RECOGNITION_REPORT_OUTPUT_MODE,
    }


def normalize_recognition_report_layout(raw_layout: Any | None = None) -> dict[str, Any]:
    default_layout = default_recognition_report_layout()
    payload = raw_layout if isinstance(raw_layout, dict) else {}
    source_columns = payload.get("columns")
    if not isinstance(source_columns, list):
        source_columns = []

    columns: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for source_column in source_columns:
        if not isinstance(source_column, dict):
            continue
        key = str(source_column.get("key") or "")
        definition = RECOGNITION_REPORT_FIELD_DEFINITIONS.get(key)
        if definition is None or key in used_keys:
            continue
        used_keys.add(key)
        label = str(source_column.get("label") or definition["label"]).strip() or definition["label"]
        if label in RECOGNITION_REPORT_LEGACY_LABELS.get(key, set()):
            label = definition["label"]
        columns.append(
            {
                "key": key,
                "label": label[:40],
                "visible": source_column.get("visible") is not False,
                "width": bounded_int(source_column.get("width"), int(definition["width"]), 8, 60),
            }
        )

    for key in RECOGNITION_REPORT_DEFAULT_FIELD_ORDER:
        if key in used_keys:
            continue
        definition = RECOGNITION_REPORT_FIELD_DEFINITIONS[key]
        columns.append(
            {
                "key": key,
                "label": definition["label"],
                "visible": True,
                "width": definition["width"],
            }
        )

    if not any(column["visible"] for column in columns):
        for column in columns:
            column["visible"] = True

    output_mode = str(payload.get("output_mode", payload.get("outputMode")) or default_layout["output_mode"])
    if output_mode not in RECOGNITION_REPORT_OUTPUT_MODES:
        output_mode = str(default_layout["output_mode"])

    return {
        "columns": columns,
        "header_row_height": bounded_int(
            payload.get("header_row_height", payload.get("headerRowHeight")),
            int(default_layout["header_row_height"]),
            18,
            80,
        ),
        "row_height": bounded_int(
            payload.get("row_height", payload.get("rowHeight")),
            int(default_layout["row_height"]),
            24,
            180,
        ),
        "image_width": bounded_int(
            payload.get("image_width", payload.get("imageWidth")),
            int(default_layout["image_width"]),
            32,
            220,
        ),
        "image_height": bounded_int(
            payload.get("image_height", payload.get("imageHeight")),
            int(default_layout["image_height"]),
            32,
            220,
        ),
        "image_offset_x": bounded_int(
            payload.get("image_offset_x", payload.get("imageOffsetX")),
            int(default_layout["image_offset_x"]),
            0,
            220,
        ),
        "image_offset_y": bounded_int(
            payload.get("image_offset_y", payload.get("imageOffsetY")),
            int(default_layout["image_offset_y"]),
            0,
            220,
        ),
        "stack_sales_attr1": bool(payload.get("stack_sales_attr1", payload.get("stackSalesAttr1", False))),
        "stack_sales_attr2": bool(payload.get("stack_sales_attr2", payload.get("stackSalesAttr2", False))),
        "output_mode": output_mode,
    }


def recognition_report_layout_from_query(layout: str | None) -> dict[str, Any]:
    if not layout:
        return normalize_recognition_report_layout()
    try:
        parsed = json.loads(layout)
    except json.JSONDecodeError:
        return normalize_recognition_report_layout()
    return normalize_recognition_report_layout(parsed)


def visible_recognition_report_columns(layout: dict[str, Any]) -> list[dict[str, Any]]:
    return [column for column in layout["columns"] if column.get("visible") is not False]


def recognition_status_label(status_text: str) -> str:
    return {
        "matched": "已匹配",
        "product_unmatched": "商品未命中",
        "sku_unmatched": "SKU未命中",
        "conflict": "冲突",
    }.get(status_text, status_text or "-")


def recognition_image_label(row: dict[str, Any]) -> str:
    return text_value(row.get("image_label"))


def recognition_stall_name(row: dict[str, Any]) -> str:
    return text_value(row.get("stall_name")) or "未设置档口"


def report_quantity_value(value: Any, *, default: int = 1) -> int:
    text = text_value(value)
    if not text:
        return default
    compact = re.sub(r"\s+", "", text)
    match = re.fullmatch(r"[*xX×]?(\d+)(?:件|个|個|双|雙|条|條|套|只|瓶|包|箱)?", compact)
    if not match:
        return default
    parsed = int(match.group(1))
    return parsed if parsed > 0 else default


def report_quantity_default(row: dict[str, Any]) -> int:
    if (int_value(row.get("item_count")) or 0) > 1 and not text_value(row.get("quantity_text")):
        return 0
    return 1


def report_spec_text(row: dict[str, Any]) -> str:
    return text_value(row.get("sales_attr1_text")) or "-"


def report_sales_attr2_values(value: Any) -> list[str]:
    text = text_value(value)
    if not text:
        return ["-"]
    return [text]


def natural_report_sort_key(value: Any) -> tuple[int, float | str, str]:
    text = text_value(value) or "-"
    match = re.search(r"\d+(?:\.\d+)?", text)
    if match:
        return (0, float(match.group(0)), text.lower())
    return (1, text.lower(), text.lower())


def sorted_report_values(values: list[str]) -> list[str]:
    return sorted([value for value in values if value], key=natural_report_sort_key)


def unique_joined_report_values(values: list[Any], separator: str = "\n") -> str:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = text_value(value)
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return separator.join(unique)


def expanded_sales_attr2_values(row: dict[str, Any]) -> list[str]:
    tokens = report_sales_attr2_values(row.get("sales_attr2_text"))
    quantity = report_quantity_value(row.get("quantity_text"), default=report_quantity_default(row))
    if len(tokens) > 1:
        return tokens
    return [tokens[0] or "-"] * quantity


def recognition_report_required_value(value: Any) -> str:
    text = text_value(value)
    return "" if text in {"", "-"} else text


def recognition_report_row_is_exportable(row: dict[str, Any]) -> bool:
    if row.get("status") != "matched":
        return False
    return bool(
        recognition_report_required_value(row.get("sales_attr1_text"))
        and recognition_report_required_value(row.get("sales_attr2_text"))
    )


def recognition_report_base_line_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_category": text_value(row.get("product_name")) or "-",
        "product_id": int_value(row.get("product_id")),
        "candidate_key": text_value(row.get("candidate_key")),
        "stall_id": int_value(row.get("stall_id")),
        "stall_name": recognition_stall_name(row),
        "spec": report_spec_text(row),
        "image_label": recognition_image_label(row),
        "sku_id": int_value(row.get("sku_id")),
        "sku_image_asset_id": int_value(row.get("sku_image_asset_id")),
        "size_text": text_value(row.get("sales_attr2_text")) or "-",
        "quantity": report_quantity_value(row.get("quantity_text"), default=report_quantity_default(row)),
        "remark_text": text_value(row.get("remark_text")),
        "image_match_text": text_value(row.get("image_match_text")),
    }


def recognition_report_line_items(
    rows: list[dict[str, Any]],
    layout: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    normalized_layout = normalize_recognition_report_layout(layout)
    report_rows: list[dict[str, Any]] = []
    for row in rows:
        if not recognition_report_row_is_exportable(row):
            continue
        report_rows.append(recognition_report_base_line_item(row))

    if not normalized_layout["stack_sales_attr1"]:
        return report_rows

    grouped: dict[str, dict[str, Any]] = {}
    for row in report_rows:
        key = ":".join(
            [
                text_value(row.get("stall_id")) or text_value(row.get("stall_name")),
                text_value(row.get("product_id")) or text_value(row.get("product_category")),
                text_value(row.get("sku_id")) or text_value(row.get("spec")),
                text_value(row.get("sku_image_asset_id")) or "0",
                "grouped",
            ]
        )
        group = grouped.setdefault(
            key,
            {
                **row,
                "spec_values": [],
                "size_values": [],
                "remark_values": [],
                "image_match_text_values": [],
                "quantity": 0,
            },
        )
        group["spec_values"].append(text_value(row.get("spec")))
        group["size_values"].extend(
            expanded_sales_attr2_values(
                {
                    "sales_attr2_text": row.get("size_text"),
                    "quantity_text": row.get("quantity"),
                    "item_count": 1,
                }
            )
        )
        group["remark_values"].append(row.get("remark_text"))
        group["image_match_text_values"].append(row.get("image_match_text"))
        group["quantity"] += int_value(row.get("quantity")) or 0

    merged_rows: list[dict[str, Any]] = []
    for group in grouped.values():
        spec_values = list(group.pop("spec_values", []))
        size_values = list(group.pop("size_values", []))
        remark_values = list(group.pop("remark_values", []))
        image_match_text_values = list(group.pop("image_match_text_values", []))
        group["spec"] = " ".join(sorted_report_values(list(dict.fromkeys(spec_values)))) or "-"
        group["size_text"] = (
            " ".join(sorted_report_values(list(dict.fromkeys(size_values))))
            if normalized_layout["stack_sales_attr2"]
            else " ".join(sorted_report_values(size_values))
        ) or "-"
        group["remark_text"] = unique_joined_report_values(remark_values)
        group["image_match_text"] = unique_joined_report_values(image_match_text_values)
        merged_rows.append(group)

    return sorted(
        merged_rows,
        key=lambda row: (
            natural_report_sort_key(row.get("stall_name")),
            natural_report_sort_key(row.get("product_category")),
            natural_report_sort_key(row.get("spec")),
            natural_report_sort_key(row.get("size_text")),
        ),
    )


def recognition_report_cell_value(row: dict[str, Any], field_key: str) -> Any:
    if field_key == "product_name":
        return row["product_category"]
    if field_key == "stall_name":
        return row["stall_name"]
    if field_key == "sales_attr1":
        return row["spec"]
    if field_key == "sku_image":
        return ""
    if field_key == "sales_attr2":
        return row["size_text"]
    if field_key == "quantity":
        return row["quantity"]
    if field_key == "remark":
        return row.get("remark_text", "")
    if field_key == "image_match_text":
        return row.get("image_match_text", "")
    return ""


def recognition_report_headers(layout: dict[str, Any] | None = None) -> list[str]:
    normalized_layout = normalize_recognition_report_layout(layout)
    return [str(column["label"]) for column in visible_recognition_report_columns(normalized_layout)]


def recognition_report_export_rows(
    rows: list[dict[str, Any]],
    layout: dict[str, Any] | None = None,
) -> list[list[Any]]:
    normalized_layout = normalize_recognition_report_layout(layout)
    columns = visible_recognition_report_columns(normalized_layout)
    return recognition_report_export_rows_from_line_items(
        recognition_report_line_items(rows, normalized_layout),
        normalized_layout,
    )


def recognition_report_export_rows_from_line_items(
    report_rows: list[dict[str, Any]],
    layout: dict[str, Any] | None = None,
) -> list[list[Any]]:
    normalized_layout = normalize_recognition_report_layout(layout)
    columns = visible_recognition_report_columns(normalized_layout)
    return [
        [
            recognition_report_cell_value(row, str(column["key"]))
            for column in columns
        ]
        for row in report_rows
    ]


def recognition_report_rows_by_stall(report_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in report_rows:
        grouped.setdefault(recognition_stall_name(row), []).append(row)
    if grouped:
        return grouped
    return {"未设置档口": []}


def recognition_exception_export_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    return [
        [text_value(row.get("image_match_text")) or text_value(row.get("reason"))]
        for row in rows
        if not recognition_report_row_is_exportable(row)
    ]


def recognition_report_image_path(image: ImageAsset) -> Path | None:
    storage_root = Path(get_settings().storage_root).resolve()
    image_path = Path(image.file_path).resolve()
    if not image_path.is_relative_to(storage_root) or not image_path.is_file():
        return None
    return image_path


def recognition_report_image_buffer(
    image_path: Path,
    *,
    image_width: int = REPORT_IMAGE_SIZE,
    image_height: int = REPORT_IMAGE_SIZE,
) -> BytesIO | None:
    try:
        with PillowImage.open(image_path) as source:
            source.thumbnail((image_width, image_height))
            converted = source.convert("RGB")
            buffer = BytesIO()
            converted.save(buffer, format="PNG")
            buffer.seek(0)
            return buffer
    except (OSError, UnidentifiedImageError):
        return None


def attach_recognition_report_images(
    sheet,
    rows: list[dict[str, Any]],
    images_by_id: dict[int, ImageAsset],
    image_buffers: list[BytesIO],
    layout: dict[str, Any] | None = None,
) -> None:
    if not rows:
        return
    normalized_layout = normalize_recognition_report_layout(layout)
    image_column_index = next(
        (
            index
            for index, column in enumerate(visible_recognition_report_columns(normalized_layout), start=1)
            if column["key"] == "sku_image"
        ),
        None,
    )
    if image_column_index is None:
        return
    image_width = int(normalized_layout["image_width"])
    image_height = int(normalized_layout["image_height"])
    image_offset_x = int(normalized_layout["image_offset_x"])
    image_offset_y = int(normalized_layout["image_offset_y"])
    for row_number, row in enumerate(rows, start=2):
        image_asset_id = int_value(row.get("sku_image_asset_id"))
        if image_asset_id is None:
            continue
        image = images_by_id.get(image_asset_id)
        if image is None:
            continue
        image_path = recognition_report_image_path(image)
        if image_path is None:
            continue
        buffer = recognition_report_image_buffer(
            image_path,
            image_width=image_width,
            image_height=image_height,
        )
        if buffer is None:
            continue
        image_buffers.append(buffer)
        worksheet_image = WorksheetImage(buffer)
        worksheet_image.width = image_width
        worksheet_image.height = image_height
        worksheet_image.anchor = OneCellAnchor(
            _from=AnchorMarker(
                col=image_column_index - 1,
                colOff=pixels_to_EMU(image_offset_x),
                row=row_number - 1,
                rowOff=pixels_to_EMU(image_offset_y),
            ),
            ext=XDRPositiveSize2D(
                cx=pixels_to_EMU(image_width),
                cy=pixels_to_EMU(image_height),
            ),
        )
        sheet.add_image(worksheet_image)


def recognition_report_image_assets(
    db: Session,
    *,
    workspace_id: int,
    rows: list[dict[str, Any]],
) -> dict[int, ImageAsset]:
    image_asset_ids = sorted(
        {
            image_asset_id
            for row in rows
            if (image_asset_id := int_value(row.get("sku_image_asset_id"))) is not None
        }
    )
    if not image_asset_ids:
        return {}
    return {
        image.id: image
        for image in db.scalars(
            select(ImageAsset).where(
                ImageAsset.workspace_id == workspace_id,
                ImageAsset.id.in_(image_asset_ids),
                ImageAsset.is_deleted.is_(False),
            )
        ).all()
    }


def product_sku_linking_result_payloads(detail: StandardDetail) -> list[dict[str, Any]]:
    values = detail.field_values or {}
    results = values.get(EXPORT_PRODUCT_SKU_LINKING_RESULTS_KEY)
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]

    result = values.get(EXPORT_PRODUCT_SKU_LINKING_RESULT_KEY)
    if isinstance(result, dict):
        return [result]

    return []


def export_standard_fields_from_result(result: dict[str, Any]) -> dict[str, Any]:
    standard_fields = result.get("standard_fields")
    if isinstance(standard_fields, dict):
        return standard_fields
    return {}


def export_result_value(
    result: dict[str, Any],
    standard_fields: dict[str, Any],
    key: str,
    *fallback_keys: str,
) -> Any:
    for source_key in (key, *fallback_keys):
        value = result.get(source_key)
        if value not in (None, ""):
            return value
    return standard_fields.get(key, "")


def product_sku_linking_export_row(
    payload: dict[str, Any],
    *,
    source_identifiers: dict[str, Any],
    candidate_key_fallback: str,
    detail_number: int,
    item_index: int,
    item_count: int,
) -> dict[str, Any]:
    standard_fields = export_standard_fields_from_result(payload)
    status_text = text_value(payload.get("match_status")) or text_value(payload.get("status")) or "pending"
    image_value = payload.get("image")
    image_asset_id = int_value(payload.get("image_asset_id")) or int_value(payload.get("sku_image_asset_id"))
    image_label = text_value(payload.get("image_label"))
    if isinstance(image_value, dict):
        image_asset_id = image_asset_id or int_value(image_value.get("id"))
        image_label = image_label or text_value(image_value.get("name"))
    else:
        image_label = image_label or text_value(image_value)
    product_name = text_value(payload.get("product")) or text_value(payload.get("product_name"))
    sku_name = text_value(payload.get("sku")) or text_value(payload.get("sku_name"))
    sales_attr1 = text_value(export_result_value(payload, standard_fields, "sales_attr1", "sales_attr1_text"))
    sales_attr2 = text_value(export_result_value(payload, standard_fields, "sales_attr2", "sales_attr2_text"))
    quantity = text_value(export_result_value(payload, standard_fields, "quantity", "quantity_text"))
    remark = text_value(export_result_value(payload, standard_fields, "remark", "remark_text"))
    image_match_text = (
        text_value(payload.get("image_match_text"))
        or text_value(payload.get("match_text"))
    )
    stall_payload = payload.get("stall") if isinstance(payload.get("stall"), dict) else {}
    stall_id = int_value(payload.get("stall_id")) or int_value(stall_payload.get("id"))
    stall_name = text_value(payload.get("stall_name")) or text_value(stall_payload.get("name"))

    return {
        "contract": EXPORT_PRODUCT_SKU_LINKING_CONTRACT,
        **source_identifiers,
        "candidate_key": text_value(payload.get("candidate_key")) or candidate_key_fallback,
        "source_label": (
            f"面单 {detail_number}-{item_index}"
            if item_count > 1
            else f"面单 {detail_number}"
        ),
        "item_index": item_index,
        "item_count": item_count,
        "product_text": text_value(standard_fields.get("product")),
        "sales_attr1_text": sales_attr1,
        "sales_attr2_text": sales_attr2,
        "quantity_text": quantity,
        "remark_text": remark,
        "image_match_text": image_match_text,
        "product_name": product_name,
        "product_id": int_value(payload.get("product_id")),
        "stall_id": stall_id,
        "stall_name": stall_name,
        "sku_id": int_value(payload.get("sku_id")),
        "sku_name": sku_name,
        "sku_image_asset_id": image_asset_id,
        "image_label": image_label,
        "status": status_text,
        "reason": text_value(payload.get("exception_reason")) or text_value(payload.get("reason")),
        "match_type": "product_sku_linking_result",
        "match_field": "",
        "match_keyword": "",
    }


def product_sku_linking_result_row(
    detail: StandardDetail,
    result: dict[str, Any],
    *,
    detail_number: int,
    item_index: int,
    item_count: int,
) -> dict[str, Any]:
    return product_sku_linking_export_row(
        result,
        source_identifiers={"detail_id": detail.id},
        candidate_key_fallback=f"{detail.id}:{item_index}",
        detail_number=detail_number,
        item_index=item_index,
        item_count=item_count,
    )


def pending_product_sku_linking_row(detail: StandardDetail, *, detail_number: int) -> dict[str, Any]:
    message = "等待 Product/SKU Linking 模块输出后才能生成报货表。"
    return {
        "contract": EXPORT_PRODUCT_SKU_LINKING_CONTRACT,
        "detail_id": detail.id,
        "candidate_key": f"{detail.id}:pending-product-sku-linking",
        "source_label": f"面单 {detail_number}",
        "item_index": 1,
        "item_count": 1,
        "product_text": "",
        "sales_attr1_text": "",
        "sales_attr2_text": "",
        "quantity_text": "",
        "remark_text": "",
        "image_match_text": f"面单 {detail_number}：{message}",
        "product_name": "",
        "product_id": None,
        "sku_id": None,
        "sku_name": "",
        "sku_image_asset_id": None,
        "image_label": "",
        "status": EXPORT_PRODUCT_SKU_LINKING_PENDING_STATUS,
        "reason": message,
        "match_type": "product_sku_linking_result",
        "match_field": "",
        "match_keyword": "",
    }


def recognition_rows_from_product_sku_linking_results(details: list[StandardDetail]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for detail_number, detail in enumerate(details, start=1):
        payloads = product_sku_linking_result_payloads(detail)
        if not payloads:
            rows.append(pending_product_sku_linking_row(detail, detail_number=detail_number))
            continue
        item_count = len(payloads)
        for item_index, result in enumerate(payloads, start=1):
            rows.append(
                product_sku_linking_result_row(
                    detail,
                    result,
                    detail_number=detail_number,
                    item_index=item_index,
                    item_count=item_count,
                )
            )
    return rows


def pending_unmapped_waybill_product_sku_linking_row(
    sample: dict[str, Any],
    *,
    detail_number: int,
) -> dict[str, Any]:
    sample_text = text_value(sample.get("sample_text"))
    message = "这张面单还没有生成五字段结果，无法进入商品匹配。"
    source_label = f"面单 {detail_number}"
    return {
        "contract": EXPORT_PRODUCT_SKU_LINKING_CONTRACT,
        "detail_id": None,
        "raw_record_id": int_value(sample.get("raw_record_id")),
        "sample_id": text_value(sample.get("sample_id")),
        "candidate_key": f"{text_value(sample.get('sample_id')) or detail_number}:pending-order-row",
        "source_label": source_label,
        "item_index": 1,
        "item_count": 1,
        "product_text": "",
        "sales_attr1_text": "",
        "sales_attr2_text": "",
        "quantity_text": "",
        "remark_text": "",
        "image_match_text": f"{source_label}：{message}{(' ' + sample_text) if sample_text else ''}",
        "product_name": "",
        "product_id": None,
        "sku_id": None,
        "sku_name": "",
        "sku_image_asset_id": None,
        "image_label": "",
        "status": EXPORT_PRODUCT_SKU_LINKING_PENDING_STATUS,
        "reason": message,
        "match_type": "product_sku_linking_result",
        "match_field": "",
        "match_keyword": "",
    }


def unmapped_waybill_samples_for_task(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
    mapped_raw_record_ids: set[int],
    mapped_sample_ids: set[str],
) -> list[dict[str, Any]]:
    raw_records = db.scalars(
        select(RawCaptureRecord)
        .where(
            RawCaptureRecord.workspace_id == workspace_id,
            RawCaptureRecord.task_id == task_id,
            RawCaptureRecord.is_deleted.is_(False),
            RawCaptureRecord.archived_at.is_(None),
        )
        .order_by(RawCaptureRecord.id.asc())
    ).all()

    unmapped_samples: list[dict[str, Any]] = []
    for raw_record in raw_records:
        samples = read_waybill_samples(raw_record)
        if len(samples) == 1 and int(raw_record.id) in mapped_raw_record_ids:
            continue
        for sample in samples:
            sample_id = text_value(sample.get("sample_id"))
            raw_record_id = int_value(sample.get("raw_record_id"))
            if sample_id and sample_id in mapped_sample_ids:
                continue
            if raw_record_id in mapped_raw_record_ids:
                continue
            unmapped_samples.append(sample)
    return unmapped_samples


def export_recognition_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    summary = recognition_summary(rows)
    for row in rows:
        status_text = text_value(row.get("status"))
        if status_text and status_text not in summary:
            summary[status_text] = 0
        if status_text and status_text not in {
            "total",
            "matched",
            "product_unmatched",
            "sku_unmatched",
            "conflict",
        }:
            summary[status_text] += 1
    return summary


CHILD_SOURCE_LABEL_SUFFIX_PATTERN = re.compile(r"-子\d+$")


def recognition_waybill_count(rows: list[dict[str, Any]]) -> int:
    parent_labels: set[str] = set()
    for row in rows:
        source_label = text_value(row.get("source_label"))
        if not source_label:
            continue
        parent_label = CHILD_SOURCE_LABEL_SUFFIX_PATTERN.sub("", source_label)
        if parent_label:
            parent_labels.add(parent_label)
    return len(parent_labels) or len(rows)


def recognition_row_from_product_matching_preview(
    row: dict[str, Any],
    source: dict[str, Any],
    *,
    fallback_number: int,
) -> dict[str, Any]:
    payload = exportable_product_sku_linking_result(row)
    item_index = int_value(source.get("item_index")) or int_value(source.get("child_index")) or 1
    item_count = int_value(source.get("item_count")) or int_value(source.get("child_count")) or 1
    child_label = text_value(source.get("child_label"))
    raw_record_id = int_value(source.get("raw_record_id"))
    standard_detail = source.get("standard_detail")
    detail_id = standard_detail.id if isinstance(standard_detail, StandardDetail) else None
    result = product_sku_linking_export_row(
        payload,
        source_identifiers={
            "detail_id": detail_id,
            "raw_record_id": raw_record_id,
            "sample_id": child_label,
        },
        candidate_key_fallback=child_label or f"order-row:{fallback_number}",
        detail_number=fallback_number,
        item_index=item_index,
        item_count=item_count,
    )
    if child_label:
        result["source_label"] = child_label
    source_row = source.get("row")
    source_status = text_value(getattr(source_row, "status", ""))
    if source_status == "special":
        result["status"] = "special"
        result["reason"] = text_value(getattr(source_row, "review_reason", "")) or "特殊面单，不进入商品/SKU/图片匹配。"
    return result


def recognition_rows_from_current_order_rows(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
) -> list[dict[str, Any]]:
    scope = ProductMatchingScope(
        scope_type="current_batch",
        task_id=task_id,
        confirmed_by_user=True,
    )
    rows, sources = product_sku_rows_for_preview(
        db,
        workspace_id=workspace_id,
        payload=ProductSkuLinkingPreviewRequest(scope=scope),
    )
    rules = saved_product_sku_rule_payloads(db, workspace_id=workspace_id)
    preview = preview_product_sku_with_rules(db, workspace_id=workspace_id, rows=rows, rules=rules)
    return [
        recognition_row_from_product_matching_preview(row, source, fallback_number=index)
        for index, (row, source) in enumerate(zip(preview["rows"], sources, strict=False), start=1)
    ]


def recognition_rows_for_task(db: Session, *, workspace_id: int, task_id: int) -> list[dict[str, Any]]:
    return recognition_rows_from_current_order_rows(db, workspace_id=workspace_id, task_id=task_id)


def task_or_404(db: Session, task_id: int, workspace_id: int) -> CaptureTask:
    task = db.scalars(
        select(CaptureTask).where(
            CaptureTask.id == task_id,
            CaptureTask.workspace_id == workspace_id,
            CaptureTask.is_deleted.is_(False),
        )
    ).first()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Capture task not found.")
    return task


def set_capture_task_archive_state(
    db: Session,
    *,
    task: CaptureTask,
    user_id: int | None,
    archived: bool,
) -> dict[str, int]:
    archived_at = utc_now() if archived else None
    archived_by = user_id if archived else None

    task.archived_at = archived_at
    task.archived_by = archived_by
    task.updated_by = user_id

    raw_records = db.scalars(
        select(RawCaptureRecord).where(
            RawCaptureRecord.workspace_id == task.workspace_id,
            RawCaptureRecord.task_id == task.id,
            RawCaptureRecord.is_deleted.is_(False),
        )
    ).all()
    for record in raw_records:
        record.archived_at = archived_at
        record.archived_by = archived_by
        record.updated_by = user_id

    details = standard_details_for_task(
        db,
        workspace_id=task.workspace_id,
        task_id=task.id,
        include_archived=True,
    )
    for detail in details:
        detail.archived_at = archived_at
        detail.archived_by = archived_by
        detail.updated_by = user_id

    return {
        "raw_record_count": len(raw_records),
        "standard_detail_count": len(details),
    }


def maintenance_cutoff(days_before: int | None) -> datetime | None:
    if days_before is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days_before)


def capture_task_time(task: CaptureTask) -> datetime | None:
    parsed_time = parse_utc_datetime(task.ended_at) or parse_utc_datetime(task.started_at)
    if parsed_time is not None:
        return parsed_time
    if task.created_at is None:
        return None
    if task.created_at.tzinfo is None:
        return task.created_at.replace(tzinfo=timezone.utc)
    return task.created_at.astimezone(timezone.utc)


def capture_task_before_cutoff(task: CaptureTask, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    task_time = capture_task_time(task)
    if task_time is None:
        return False
    if task_time.tzinfo is None:
        task_time = task_time.replace(tzinfo=timezone.utc)
    return task_time <= cutoff


def standard_detail_task_id(detail: StandardDetail) -> int | None:
    values = detail.field_values if isinstance(detail.field_values, dict) else {}
    return int_value(values.get("capture_task_id"))


def capture_data_summary(db: Session, *, workspace_id: int) -> dict[str, Any]:
    tasks = db.scalars(
        select(CaptureTask).where(
            CaptureTask.workspace_id == workspace_id,
            CaptureTask.is_deleted.is_(False),
        )
    ).all()
    raw_records = db.scalars(
        select(RawCaptureRecord).where(
            RawCaptureRecord.workspace_id == workspace_id,
            RawCaptureRecord.is_deleted.is_(False),
        )
    ).all()
    details = db.scalars(
        select(StandardDetail).where(
            StandardDetail.workspace_id == workspace_id,
            StandardDetail.is_deleted.is_(False),
        )
    ).all()
    active_tasks = [task for task in tasks if not task.archived_at]
    archived_tasks = [task for task in tasks if task.archived_at]
    archive_ready_tasks = [
        task
        for task in active_tasks
        if task.status != "collecting"
    ]
    return {
        "active": {
            "capture_tasks": len(active_tasks),
            "archive_ready_tasks": len(archive_ready_tasks),
            "raw_records": len([record for record in raw_records if not record.archived_at]),
            "standard_details": len([detail for detail in details if not detail.archived_at]),
        },
        "archived": {
            "capture_tasks": len(archived_tasks),
            "raw_records": len([record for record in raw_records if record.archived_at]),
            "standard_details": len([detail for detail in details if detail.archived_at]),
        },
        "collecting_tasks": len([task for task in active_tasks if task.status == "collecting"]),
    }


def business_download_timestamp(now: datetime | None = None) -> str:
    source_time = now or datetime.now(timezone.utc)
    if source_time.tzinfo is None:
        source_time = source_time.replace(tzinfo=timezone.utc)
    return source_time.astimezone(BUSINESS_DOWNLOAD_TIMEZONE).strftime("%Y%m%d_%H%M%S")


def business_download_filename(
    prefix: str,
    extension: str,
    *,
    timestamp: str | None = None,
) -> str:
    clean_prefix = safe_download_name_part(prefix)
    clean_extension = extension if extension.startswith(".") else f".{extension}"
    return f"{clean_prefix}_{timestamp or business_download_timestamp()}{clean_extension}"


def xlsx_response(workbook: Workbook, filename: str) -> StreamingResponse:
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    quoted_filename = quote(filename)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=download.xlsx; filename*=UTF-8''{quoted_filename}",
        },
    )


def zip_stream_response(buffer: BytesIO, filename: str) -> StreamingResponse:
    buffer.seek(0)
    quoted_filename = quote(filename)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=download.zip; filename*=UTF-8''{quoted_filename}"},
    )


def append_xlsx_rows(sheet, headers: list[str], rows: list[list[Any]]) -> None:
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    sheet.freeze_panes = "A2"
    for column_cells in sheet.columns:
        column_letter = column_cells[0].column_letter
        max_length = max(len(str(cell.value or "")) for cell in column_cells[:80])
        sheet.column_dimensions[column_letter].width = min(max(max_length + 2, 12), 60)


def style_recognition_report_sheet(sheet, layout: dict[str, Any] | None = None) -> None:
    normalized_layout = normalize_recognition_report_layout(layout)
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(color="FFFFFF", bold=True)
    thin_border = Border(
        left=Side(style="thin", color="D9E2F3"),
        right=Side(style="thin", color="D9E2F3"),
        top=Side(style="thin", color="D9E2F3"),
        bottom=Side(style="thin", color="D9E2F3"),
    )
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for column_index, column in enumerate(visible_recognition_report_columns(normalized_layout), start=1):
        sheet.column_dimensions[get_column_letter(column_index)].width = report_layout_width_to_excel_width(column["width"])

    sheet.row_dimensions[1].height = report_layout_height_to_excel_points(normalized_layout["header_row_height"])

    for row in sheet.iter_rows():
        for cell in row:
            cell.alignment = center
            cell.border = thin_border
            if cell.row == 1:
                cell.fill = header_fill
                cell.font = header_font
            elif not cell.value:
                cell.value = None

    for row_number in range(2, sheet.max_row + 1):
        sheet.row_dimensions[row_number].height = report_layout_height_to_excel_points(normalized_layout["row_height"])


def safe_excel_sheet_title(value: str, used_titles: set[str]) -> str:
    base = re.sub(r"[\[\]\:\*\?/\\]", "_", text_value(value) or "未设置档口").strip("' ") or "未设置档口"
    title = base[:31]
    suffix = 2
    while title in used_titles:
        marker = f"_{suffix}"
        title = f"{base[:31 - len(marker)]}{marker}"
        suffix += 1
    used_titles.add(title)
    return title


def safe_download_name_part(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text_value(value) or "未设置档口").strip() or "未设置档口"


def append_recognition_report_sheet(
    workbook: Workbook,
    *,
    title: str,
    report_rows: list[dict[str, Any]],
    report_layout: dict[str, Any],
    images_by_id: dict[int, ImageAsset],
    image_buffers: list[BytesIO],
    used_titles: set[str],
) -> None:
    sheet = workbook.create_sheet(safe_excel_sheet_title(title, used_titles))
    append_xlsx_rows(
        sheet,
        recognition_report_headers(report_layout),
        recognition_report_export_rows_from_line_items(report_rows, report_layout),
    )
    style_recognition_report_sheet(sheet, report_layout)
    attach_recognition_report_images(sheet, report_rows, images_by_id, image_buffers, report_layout)


def recognition_report_workbook(
    *,
    report_rows: list[dict[str, Any]],
    report_layout: dict[str, Any],
    images_by_id: dict[int, ImageAsset],
    sheet_title: str = "报货表",
) -> Workbook:
    workbook = Workbook()
    workbook.remove(workbook.active)
    image_buffers: list[BytesIO] = []
    append_recognition_report_sheet(
        workbook,
        title=sheet_title,
        report_rows=report_rows,
        report_layout=report_layout,
        images_by_id=images_by_id,
        image_buffers=image_buffers,
        used_titles=set(),
    )
    workbook._recognition_image_buffers = image_buffers  # type: ignore[attr-defined]
    return workbook


def append_recognition_exception_sheet(workbook: Workbook, exception_rows: list[list[Any]]):
    sheet = workbook.create_sheet(RECOGNITION_EXCEPTION_SHEET_TITLE)
    append_xlsx_rows(sheet, RECOGNITION_EXCEPTION_HEADERS, exception_rows)
    return sheet


def standard_details_for_task(
    db: Session,
    *,
    workspace_id: int,
    task_id: int,
    include_archived: bool = False,
) -> list[StandardDetail]:
    statement = select(StandardDetail).where(
        StandardDetail.workspace_id == workspace_id,
        StandardDetail.is_deleted.is_(False),
    )
    if not include_archived:
        statement = statement.where(StandardDetail.archived_at.is_(None))
    return [
        detail
        for detail in db.scalars(statement.order_by(StandardDetail.id.asc())).all()
        if int((detail.field_values or {}).get("capture_task_id") or 0) == task_id
    ]


def get_collector_from_token(
    db: Session,
    x_collector_token: str | None,
    *,
    identity_hint: str | None = None,
    allow_identity_rebind: bool = False,
) -> Collector:
    if not x_collector_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing collector token.")

    token_hash = hash_collector_token(x_collector_token)
    collector = db.scalars(
        select(Collector).where(
            Collector.token_hash == token_hash,
            Collector.is_enabled.is_(True),
            Collector.is_deleted.is_(False),
        )
    ).first()
    if collector is None:
        identity = str(identity_hint or "").strip()
        if allow_identity_rebind and identity:
            identity_matches = db.scalars(
                select(Collector).where(
                    Collector.is_enabled.is_(True),
                    Collector.is_deleted.is_(False),
                    (Collector.collector_id == identity) | (Collector.source_machine == identity),
                )
            ).all()
            if len(identity_matches) == 1:
                collector = identity_matches[0]
                collector.token_hash = token_hash
                status_payload = collector.status_payload if isinstance(collector.status_payload, dict) else {}
                collector.status_payload = {
                    **status_payload,
                    "token_rebound_at": utc_now(),
                    "token_rebound_reason": "collector_identity_match",
                }
                return collector
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid collector token.")
    return collector


def active_task_statement(workspace_id: int, collector_db_id: int | None = None):
    statement = select(CaptureTask).where(
        CaptureTask.workspace_id == workspace_id,
        CaptureTask.status == "collecting",
        CaptureTask.is_deleted.is_(False),
    )
    if collector_db_id is not None:
        statement = statement.where(
            (CaptureTask.collector_id.is_(None)) | (CaptureTask.collector_id == collector_db_id)
        )
    return statement.order_by(CaptureTask.id.desc())


def upsert_collector(
    db: Session,
    *,
    tenant_id: int | None,
    workspace_id: int,
    payload: CollectorRegisterRequest,
    user_id: int | None,
) -> tuple[Collector, str]:
    token = create_collector_token()
    token_hash = hash_collector_token(token)
    collector_identity = clean_optional_text(payload.collector_id) or f"collector-{token[:12]}"
    source_machine = clean_optional_text(payload.source_machine) or None
    display_name = collector_display_name(
        payload.collector_name,
        source_machine=source_machine,
        collector_id=collector_identity,
    )

    collector = db.scalars(
        select(Collector).where(
            Collector.workspace_id == workspace_id,
            Collector.collector_id == collector_identity,
            Collector.is_deleted.is_(False),
        )
    ).first()
    if collector is None:
        collector = Collector(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            collector_id=collector_identity,
            collector_name=display_name,
            token_hash=token_hash,
            source_machine=source_machine,
            client_version=payload.client_version,
            online_status="offline",
            remark=payload.remark,
            created_by=user_id,
            updated_by=user_id,
        )
        db.add(collector)
    else:
        if is_default_collector_display_name(collector.collector_name) or not is_default_collector_display_name(
            payload.collector_name
        ):
            collector.collector_name = display_name
        collector.token_hash = token_hash
        collector.source_machine = source_machine
        collector.client_version = payload.client_version
        collector.is_enabled = True
        collector.remark = payload.remark
        collector.updated_by = user_id

    db.commit()
    db.refresh(collector)
    return collector, token


def collector_identity_is_available(
    db: Session,
    *,
    workspace_id: int,
    collector_identity: str,
    current_collector_id: int,
) -> bool:
    existing = db.scalars(
        select(Collector).where(
            Collector.workspace_id == workspace_id,
            Collector.collector_id == collector_identity,
            Collector.id != current_collector_id,
            Collector.is_deleted.is_(False),
        )
    ).first()
    return existing is None


@router.get("/collector-client/download")
def download_collector_client(
    mode: str = Query(default="cli", pattern="^(cli|script|exe)$"),
    _current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    archive = build_collector_client_archive(mode)
    content = archive.getvalue()
    filename = quote("订单整理系统采集器.zip")
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Content-Length": str(len(content)),
        },
    )


@router.post("/collector-control/register", status_code=status.HTTP_201_CREATED)
def register_collector(
    payload: CollectorRegisterRequest = Body(default_factory=CollectorRegisterRequest),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    tenant_id = get_workspace_tenant_id(db, workspace_id)
    collector, token = upsert_collector(
        db,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        payload=payload,
        user_id=current_user.id,
    )
    return {"collector": public_collector(collector), "collector_token": token}


@router.get("/collector-control/status")
def collector_status(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    cleanup_expired_collectors(db, workspace_id=workspace_id, user_id=current_user.id)
    collectors = db.scalars(
        select(Collector)
        .where(Collector.workspace_id == workspace_id, Collector.is_deleted.is_(False))
        .order_by(Collector.id.desc())
    ).all()
    active_task = db.scalars(active_task_statement(workspace_id)).first()
    return {
        "collectors": [public_collector(collector) for collector in collectors],
        "active_task": public_task(active_task) if active_task else None,
        "collector_client": collector_client_release_status(),
    }


@router.post("/collector-control/start", status_code=status.HTTP_201_CREATED)
def start_capture(
    payload: CaptureStartRequest = Body(default_factory=CaptureStartRequest),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    existing = db.scalars(active_task_statement(workspace_id)).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A capture task is already collecting.")

    tenant_id = get_workspace_tenant_id(db, workspace_id)
    if payload.collector_id is not None:
        collector = db.get(Collector, payload.collector_id)
        if collector is None or collector.workspace_id != workspace_id or collector.is_deleted:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Collector access denied.")

    task = CaptureTask(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        name=payload.name or f"采集任务 {utc_now()}",
        collector_id=payload.collector_id,
        status="collecting",
        started_at=utc_now(),
        config={"started_by": current_user.id},
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return public_task(task)


@router.post("/collector-control/stop")
def stop_capture(
    payload: CaptureStopRequest = Body(default_factory=CaptureStopRequest),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    if payload.task_id is None:
        task = db.scalars(active_task_statement(workspace_id)).first()
    else:
        task = db.scalars(
            select(CaptureTask).where(
                CaptureTask.id == payload.task_id,
                CaptureTask.workspace_id == workspace_id,
                CaptureTask.is_deleted.is_(False),
            )
        ).first()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active capture task not found.")
    if task.status != "collecting":
        return public_task(task)

    config = dict(task.config or {})
    config["ended_by"] = current_user.id
    task.config = config
    task.status = "completed"
    task.ended_at = utc_now()
    task.updated_by = current_user.id
    db.commit()
    db.refresh(task)
    return public_task(task)


@router.get("/system-settings/data-maintenance")
def data_maintenance_summary(
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    return capture_data_summary(db, workspace_id=workspace_id)


@router.post("/system-settings/data-maintenance/archive-capture-data")
def archive_capture_data(
    payload: ArchiveCaptureDataRequest = Body(default_factory=ArchiveCaptureDataRequest),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    cutoff = maintenance_cutoff(payload.days_before)
    tasks = [
        task
        for task in db.scalars(
            select(CaptureTask)
            .where(
                CaptureTask.workspace_id == workspace_id,
                CaptureTask.is_deleted.is_(False),
                CaptureTask.archived_at.is_(None),
                CaptureTask.status != "collecting",
            )
            .order_by(CaptureTask.id.asc())
        ).all()
        if capture_task_before_cutoff(task, cutoff)
    ]

    archived_raw_records = 0
    archived_standard_details = 0
    for task in tasks:
        counts = set_capture_task_archive_state(
            db,
            task=task,
            user_id=current_user.id,
            archived=True,
        )
        archived_raw_records += counts["raw_record_count"]
        archived_standard_details += counts["standard_detail_count"]

    db.commit()
    return {
        "archived_capture_tasks": len(tasks),
        "archived_raw_records": archived_raw_records,
        "archived_standard_details": archived_standard_details,
        "summary": capture_data_summary(db, workspace_id=workspace_id),
    }


@router.post("/system-settings/data-maintenance/delete-archived-capture-data")
def delete_archived_capture_data(
    payload: DeleteArchivedCaptureDataRequest,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    if payload.confirm_text.strip() != "删除归档数据":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="请输入确认文字：删除归档数据",
        )

    cutoff = maintenance_cutoff(payload.days_before)
    tasks = [
        task
        for task in db.scalars(
            select(CaptureTask)
            .where(
                CaptureTask.workspace_id == workspace_id,
                CaptureTask.is_deleted.is_(False),
                CaptureTask.archived_at.is_not(None),
            )
            .order_by(CaptureTask.id.asc())
        ).all()
        if capture_task_before_cutoff(task, cutoff)
    ]
    task_ids = {int(task.id) for task in tasks}
    raw_records = db.scalars(
        select(RawCaptureRecord).where(
            RawCaptureRecord.workspace_id == workspace_id,
            RawCaptureRecord.task_id.in_(task_ids),
            RawCaptureRecord.archived_at.is_not(None),
        )
    ).all() if task_ids else []
    details = [
        detail
        for detail in db.scalars(
            select(StandardDetail).where(
                StandardDetail.workspace_id == workspace_id,
                StandardDetail.archived_at.is_not(None),
            )
        ).all()
        if (standard_detail_task_id(detail) or 0) in task_ids
    ] if task_ids else []
    detail_batch_ids = {
        int(detail.standard_detail_batch_id)
        for detail in details
        if detail.standard_detail_batch_id
    }
    detail_batches = db.scalars(
        select(StandardDetailBatch).where(
            StandardDetailBatch.workspace_id == workspace_id,
            StandardDetailBatch.id.in_(detail_batch_ids),
        )
    ).all() if detail_batch_ids else []

    deleted_counts = {
        "deleted_capture_tasks": len(tasks),
        "deleted_raw_records": len(raw_records),
        "deleted_standard_details": len(details),
        "deleted_standard_detail_batches": len(detail_batches),
    }
    for detail in details:
        db.delete(detail)
    for batch in detail_batches:
        db.delete(batch)
    for record in raw_records:
        db.delete(record)
    for task in tasks:
        db.delete(task)

    db.commit()
    return {
        **deleted_counts,
        "summary": capture_data_summary(db, workspace_id=workspace_id),
    }


@router.post("/collector-control/parse-records")
def parse_capture_records(
    payload: ParseRecordsRequest = Body(default_factory=ParseRecordsRequest),
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_write),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    statement = select(RawCaptureRecord).where(
        RawCaptureRecord.workspace_id == workspace_id,
        RawCaptureRecord.is_deleted.is_(False),
        RawCaptureRecord.archived_at.is_(None),
    )
    if payload.task_id is not None:
        statement = statement.where(RawCaptureRecord.task_id == payload.task_id)
    records = db.scalars(statement.order_by(RawCaptureRecord.id.asc())).all()

    active_pack = active_recognition_rule_pack(db, workspace_id=workspace_id)
    if active_pack is None:
        return {
            "status": RULE_PACK_MISSING_STATUS,
            "rule_pack_required": True,
            "message": "当前工作空间未启用识别规则包。请先导入并启用规则包，再进行面单识别。",
            "parsed": 0,
            "skipped": 0,
            "raw_record_count": len(records),
            "task_id": payload.task_id,
        }

    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="旧面单解析入口已停用。请使用独立面单解析服务生成订单行，避免旧 standard_details 与新订单行数据混用。",
    )


@router.get("/collector-control/tasks/{task_id}/raw-document")
def download_raw_capture_document(
    task_id: int,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> StreamingResponse:
    task = task_or_404(db, task_id, workspace_id)
    records = db.scalars(
        select(RawCaptureRecord)
        .where(
            RawCaptureRecord.workspace_id == workspace_id,
            RawCaptureRecord.task_id == task.id,
            RawCaptureRecord.is_deleted.is_(False),
        )
        .order_by(RawCaptureRecord.id.asc())
    ).all()
    collector_ids = sorted({int(record.collector_id) for record in records if record.collector_id})
    collectors_by_id = {
        collector.id: collector
        for collector in db.scalars(
            select(Collector).where(
                Collector.id.in_(collector_ids),
                Collector.workspace_id == workspace_id,
                Collector.is_deleted.is_(False),
            )
        ).all()
    } if collector_ids else {}

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "原文"
    rows = [
        [
            record.id,
            raw_record_collector_label(record, collectors_by_id),
            record.source_machine,
            source_component_label(record.source_component),
            record.source_index,
            record.dedupe_key,
            record.captured_at,
            record.payload_format,
            json_text(record.source_columns),
            record.status,
            record.raw_payload,
        ]
        for record in records
    ]
    append_xlsx_rows(
        sheet,
        [
            "ID",
            "采集器",
            "电脑名",
            "来源组件",
            "来源序号",
            "去重键",
            "采集时间",
            "原文格式",
            "本地来源信息",
            "状态",
            "采集原文",
        ],
        rows,
    )
    return xlsx_response(workbook, business_download_filename("采集原文", "xlsx"))


@router.get("/collector-control/tasks/{task_id}/standard-document")
def download_standard_capture_document(
    task_id: int,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> StreamingResponse:
    task = task_or_404(db, task_id, workspace_id)
    details = standard_details_for_task(db, workspace_id=workspace_id, task_id=task.id)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "整理结果"
    export_fields = db.scalars(
        select(ExportHeaderDefinition)
        .where(
            ExportHeaderDefinition.workspace_id == workspace_id,
            ExportHeaderDefinition.export_enabled.is_(True),
            ExportHeaderDefinition.export_order > 0,
            ExportHeaderDefinition.is_deleted.is_(False),
        )
        .order_by(ExportHeaderDefinition.export_order.asc(), ExportHeaderDefinition.id.asc())
    ).all()

    if not export_fields:
        append_xlsx_rows(
            sheet,
            ["提示"],
            [["当前工作区还没有定义整理文档表头，暂不生成业务整理文档。"]],
        )
        return xlsx_response(workbook, business_download_filename("整理文档", "xlsx"))

    rows = []
    for detail in details:
        for values in standard_detail_export_rows(detail):
            rows.append(
                [export_field_value(field.code, values) for field in export_fields]
            )
    append_xlsx_rows(
        sheet,
        [field.name for field in export_fields],
        rows,
    )
    return xlsx_response(workbook, business_download_filename("整理文档", "xlsx"))


@router.get("/collector-control/tasks/{task_id}/report-preview")
@router.get("/collector-control/tasks/{task_id}/recognition-preview")
def preview_capture_task_recognition(
    task_id: int,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> dict[str, Any]:
    task = task_or_404(db, task_id, workspace_id)
    details = standard_details_for_task(db, workspace_id=workspace_id, task_id=task.id)
    rows = recognition_rows_for_task(db, workspace_id=workspace_id, task_id=task.id)
    waybill_count = recognition_waybill_count(rows)
    return {
        "task_id": task.id,
        "task_name": task.name,
        "contract": EXPORT_PRODUCT_SKU_LINKING_CONTRACT,
        "data_source": "order_row_drafts",
        "detail_count": waybill_count or len(details),
        "waybill_count": waybill_count,
        "order_row_count": len(rows),
        "rows": rows,
        "summary": export_recognition_summary(rows),
    }


@router.get("/collector-control/tasks/{task_id}/report-workbook")
@router.get("/collector-control/tasks/{task_id}/recognition-report")
def download_capture_task_recognition_report(
    task_id: int,
    layout: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(get_current_user),
    workspace_id: int = Depends(get_workspace_id),
) -> StreamingResponse:
    task = task_or_404(db, task_id, workspace_id)
    rows = recognition_rows_for_task(db, workspace_id=workspace_id, task_id=task.id)
    images_by_id = recognition_report_image_assets(db, workspace_id=workspace_id, rows=rows)
    report_layout = recognition_report_layout_from_query(layout)
    report_rows = recognition_report_line_items(rows, report_layout)
    exception_rows = recognition_exception_export_rows(rows)
    download_timestamp = business_download_timestamp()

    if report_layout["output_mode"] == "stall_workbooks":
        archive = BytesIO()
        with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
            for stall_name, stall_rows in recognition_report_rows_by_stall(report_rows).items():
                stall_workbook = recognition_report_workbook(
                    report_rows=stall_rows,
                    report_layout=report_layout,
                    images_by_id=images_by_id,
                    sheet_title=safe_download_name_part(stall_name),
                )
                workbook_buffer = BytesIO()
                stall_workbook.save(workbook_buffer)
                zip_file.writestr(
                    business_download_filename(
                        f"{safe_download_name_part(stall_name)}_{BUSINESS_REPORT_DOWNLOAD_PREFIX}",
                        "xlsx",
                        timestamp=download_timestamp,
                    ),
                    workbook_buffer.getvalue(),
                )
            exception_workbook = Workbook()
            exception_sheet = exception_workbook.active
            exception_sheet.title = RECOGNITION_EXCEPTION_SHEET_TITLE
            append_xlsx_rows(exception_sheet, RECOGNITION_EXCEPTION_HEADERS, exception_rows)
            exception_buffer = BytesIO()
            exception_workbook.save(exception_buffer)
            zip_file.writestr(
                business_download_filename(RECOGNITION_EXCEPTION_SHEET_TITLE, "xlsx", timestamp=download_timestamp),
                exception_buffer.getvalue(),
            )
        return zip_stream_response(
            archive,
            business_download_filename(f"{BUSINESS_REPORT_DOWNLOAD_PREFIX}_分档口", "zip", timestamp=download_timestamp),
        )

    workbook = Workbook()
    image_buffers: list[BytesIO] = []
    if report_layout["output_mode"] == "stall_sheet":
        workbook.remove(workbook.active)
        used_titles: set[str] = set()
        for stall_name, stall_rows in recognition_report_rows_by_stall(report_rows).items():
            append_recognition_report_sheet(
                workbook,
                title=stall_name,
                report_rows=stall_rows,
                report_layout=report_layout,
                images_by_id=images_by_id,
                image_buffers=image_buffers,
                used_titles=used_titles,
            )
    else:
        sheet = workbook.active
        sheet.title = "报货表"
        append_xlsx_rows(
            sheet,
            recognition_report_headers(report_layout),
            recognition_report_export_rows_from_line_items(report_rows, report_layout),
        )
        style_recognition_report_sheet(sheet, report_layout)
        attach_recognition_report_images(sheet, report_rows, images_by_id, image_buffers, report_layout)

    append_recognition_exception_sheet(workbook, exception_rows)
    return xlsx_response(
        workbook,
        business_download_filename(BUSINESS_REPORT_DOWNLOAD_PREFIX, "xlsx", timestamp=download_timestamp),
    )


@router.post("/collector-runtime/heartbeat")
def collector_heartbeat(
    payload: CollectorHeartbeatRequest = Body(default_factory=CollectorHeartbeatRequest),
    db: Session = Depends(get_db),
    x_collector_token: Annotated[str | None, Header(alias="X-Collector-Token")] = None,
) -> dict[str, Any]:
    collector = get_collector_from_token(
        db,
        x_collector_token,
        identity_hint=payload.collector_id or payload.source_machine,
        allow_identity_rebind=True,
    )
    collector.online_status = "online"
    collector.last_heartbeat_at = utc_now()
    collector.status_payload = {
        "runtime_status": payload.runtime_status or "unknown",
        "adapter_status": payload.adapter_status or {},
        "queue_size": payload.queue_size,
        "last_error": payload.last_error,
        "received_at": utc_now(),
    }
    if payload.source_machine:
        collector.source_machine = payload.source_machine
    reported_identity = str(payload.collector_id or payload.source_machine or "").strip()
    if reported_identity and collector_identity_is_available(
        db,
        workspace_id=collector.workspace_id,
        collector_identity=reported_identity,
        current_collector_id=collector.id,
    ):
        collector.collector_id = reported_identity
    reported_display_name = collector_display_name(
        payload.collector_name,
        source_machine=payload.source_machine,
        collector_id=reported_identity or collector.collector_id,
    )
    if is_default_collector_display_name(collector.collector_name) or not is_default_collector_display_name(
        payload.collector_name
    ):
        collector.collector_name = reported_display_name
    if payload.client_version:
        collector.client_version = payload.client_version

    tasks = db.scalars(active_task_statement(collector.workspace_id, collector.id)).all()
    db.commit()
    return {
        "collector": public_collector(collector),
        "tasks": [public_task(task) for task in tasks],
    }


@router.post("/collector-runtime/raw-records", status_code=status.HTTP_201_CREATED)
def upload_raw_records(
    payload: RawCaptureBatchRequest,
    db: Session = Depends(get_db),
    x_collector_token: Annotated[str | None, Header(alias="X-Collector-Token")] = None,
) -> dict[str, int]:
    """Persist collector payloads as raw_capture_record only.

    Waybill reading/parsing is owned by the downstream module and is triggered
    explicitly through /collector-control/parse-records.
    """
    collector = get_collector_from_token(db, x_collector_token)
    task = db.get(CaptureTask, payload.task_id)
    if (
        task is None
        or task.workspace_id != collector.workspace_id
        or task.is_deleted
        or task.archived_at
        or task.status not in {"collecting", "completed"}
        or (task.collector_id is not None and task.collector_id != collector.id)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Capture task access denied.")

    inserted = 0
    skipped = 0
    for item in payload.records:
        if item.dedupe_key:
            existing = db.scalars(
                select(RawCaptureRecord).where(
                    RawCaptureRecord.workspace_id == collector.workspace_id,
                    RawCaptureRecord.dedupe_key == item.dedupe_key,
                    RawCaptureRecord.is_deleted.is_(False),
                    RawCaptureRecord.archived_at.is_(None),
                )
            ).first()
            if existing is not None:
                skipped += 1
                continue

        record = build_raw_capture_record(
            collector=collector,
            task=task,
            payload=item,
            captured_at=utc_now(),
        )
        db.add(record)
        db.flush()
        inserted += 1

    db.commit()
    return {"inserted": inserted, "skipped": skipped}
