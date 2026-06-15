from collections import defaultdict
from decimal import Decimal
from typing import Any


def summarize_by_key(rows: list[dict[str, Any]], key_field: str, quantity_field: str) -> list[dict[str, Any]]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        key = str(row.get(key_field, ""))
        totals[key] += Decimal(str(row.get(quantity_field, 0) or 0))
    return [{"key": key, "quantity": str(quantity)} for key, quantity in totals.items()]
