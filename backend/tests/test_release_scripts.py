from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_customer_delivery_audit_uses_export_coverage_partition() -> None:
    source = (ROOT / "scripts" / "customer_delivery_audit.ps1").read_text(encoding="utf-8")

    assert 'Name "normal_export_count"' in source
    assert 'Name "exception_export_count"' in source
    assert "Report preview rows (" not in source
    assert "-and (Has-BusinessValue $salesAttr1)" not in source
