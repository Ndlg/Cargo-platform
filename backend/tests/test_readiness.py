from contextlib import nullcontext
from types import SimpleNamespace

from fastapi import HTTPException

from app.api.routes import health as health_route


class _DatabaseProbe:
    def execute(self, _statement):
        return self

    def scalar_one(self) -> int:
        return 1


class _UnavailableDatabaseProbe:
    def __enter__(self):
        raise RuntimeError("database unavailable")

    def __exit__(self, *_args):
        return False


def test_readiness_requires_database_storage_and_matching_parser(tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(app_name="Cargo Platform", app_version="1.0.0", storage_root=str(tmp_path))
    monkeypatch.setattr(health_route, "get_settings", lambda: settings)
    monkeypatch.setattr(health_route, "SessionLocal", lambda: nullcontext(_DatabaseProbe()))
    monkeypatch.setattr(
        health_route,
        "get_waybill_parser_service",
        lambda *_args, **_kwargs: {"status": "ok", "version": "1.0.0"},
    )

    result = health_route.readiness()

    assert result["status"] == "ready"
    assert result["components"] == {"database": "ok", "storage": "ok", "parser": "ok"}


def test_readiness_rejects_parser_version_mismatch(tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(app_name="Cargo Platform", app_version="1.0.0", storage_root=str(tmp_path))
    monkeypatch.setattr(health_route, "get_settings", lambda: settings)
    monkeypatch.setattr(health_route, "SessionLocal", lambda: nullcontext(_DatabaseProbe()))
    monkeypatch.setattr(
        health_route,
        "get_waybill_parser_service",
        lambda *_args, **_kwargs: {"status": "ok", "version": "0.9.0"},
    )

    try:
        health_route.readiness()
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail["components"]["parser"] == "version_mismatch"
    else:
        raise AssertionError("readiness accepted a mixed parser version")


def test_readiness_reports_each_component_independently(tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(app_name="Cargo Platform", app_version="1.0.0", storage_root=str(tmp_path))
    monkeypatch.setattr(health_route, "get_settings", lambda: settings)
    monkeypatch.setattr(health_route, "SessionLocal", _UnavailableDatabaseProbe)
    monkeypatch.setattr(
        health_route,
        "get_waybill_parser_service",
        lambda *_args, **_kwargs: {"status": "ok", "version": "1.0.0"},
    )

    try:
        health_route.readiness()
    except HTTPException as exc:
        assert exc.detail["components"] == {
            "database": "unavailable",
            "storage": "ok",
            "parser": "ok",
        }
    else:
        raise AssertionError("readiness accepted an unavailable database")


def test_readiness_reports_unavailable_parser(tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(app_name="Cargo Platform", app_version="1.0.0", storage_root=str(tmp_path))
    monkeypatch.setattr(health_route, "get_settings", lambda: settings)
    monkeypatch.setattr(health_route, "SessionLocal", lambda: nullcontext(_DatabaseProbe()))
    monkeypatch.setattr(
        health_route,
        "get_waybill_parser_service",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parser unavailable")),
    )

    try:
        health_route.readiness()
    except HTTPException as exc:
        assert exc.detail["components"] == {
            "database": "ok",
            "storage": "ok",
            "parser": "unavailable",
        }
    else:
        raise AssertionError("readiness accepted an unavailable parser")
