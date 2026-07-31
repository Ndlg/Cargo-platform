from argparse import ArgumentParser
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.api.routes.collector_runtime import (  # noqa: E402
    recognition_exception_export_rows,
    recognition_report_row_is_exportable,
    recognition_rows_for_task,
)
from app.core.database import SessionLocal  # noqa: E402
from app.models import CaptureTask  # noqa: E402
from app.services.order_row_reader import (  # noqa: E402
    order_row_sample_inputs_from_records,
    raw_records_for_task,
)
from app.services.regression_coverage import analyze_waybill_coverage  # noqa: E402


def expected_parent_documents(records: list[Any]) -> list[dict[str, int]]:
    return [
        {
            "raw_record_id": int(sample["raw_record_id"]),
            "parent_sequence": int(sample["parent_sequence"]),
        }
        for sample in order_row_sample_inputs_from_records(records)
    ]


def scan_task(db: Any, *, workspace_id: int, task_id: int) -> dict[str, Any]:
    records = raw_records_for_task(db, workspace_id=workspace_id, task_id=task_id)
    rows = recognition_rows_for_task(db, workspace_id=workspace_id, task_id=task_id)
    result = analyze_waybill_coverage(
        expected_parent_documents=expected_parent_documents(records),
        rows=rows,
        normal_export_count=sum(recognition_report_row_is_exportable(row) for row in rows),
        exception_export_count=len(recognition_exception_export_rows(rows)),
    )
    return {"task_id": task_id, **result}


def main() -> int:
    parser = ArgumentParser(description="Read-only waybill coverage regression gate.")
    parser.add_argument("--workspace-id", type=int, default=1)
    parser.add_argument("--task-id", type=int, action="append", default=[])
    args = parser.parse_args()

    with SessionLocal() as db:
        query = select(CaptureTask).where(
            CaptureTask.workspace_id == args.workspace_id,
            CaptureTask.status == "completed",
            CaptureTask.is_deleted.is_(False),
        )
        if args.task_id:
            query = query.where(CaptureTask.id.in_(set(args.task_id)))
        tasks = db.scalars(query.order_by(CaptureTask.id.asc())).all()
        found_ids = {int(task.id) for task in tasks}
        missing_task_ids = sorted(set(args.task_id) - found_ids)
        results: list[dict[str, Any]] = []
        for task in tasks:
            try:
                results.append(scan_task(db, workspace_id=args.workspace_id, task_id=int(task.id)))
            except Exception as exc:
                results.append(
                    {
                        "task_id": int(task.id),
                        "ok": False,
                        "failures": [{"code": "scan_error", "count": 1}],
                        "warnings": [],
                        "error_type": type(exc).__name__,
                    }
                )

    totals = Counter()
    for result in results:
        for key in (
            "expected_parent_count",
            "recognized_parent_count",
            "covered_parent_count",
            "normal_parent_count",
            "exception_parent_count",
            "result_row_count",
            "normal_export_count",
            "exception_export_count",
        ):
            totals[key] += int(result.get(key) or 0)
    payload = {
        "ok": not missing_task_ids and all(result["ok"] for result in results),
        "workspace_id": args.workspace_id,
        "task_count": len(results),
        "missing_task_ids": missing_task_ids,
        "totals": dict(totals),
        "tasks": results,
    }
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
