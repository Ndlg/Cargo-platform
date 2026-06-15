from collections.abc import Generator

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models.base import Base


def _connect_args(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def create_db_engine():
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args=_connect_args(settings.database_url),
    )


engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _run_sqlite_compat_migrations()


def _run_sqlite_compat_migrations() -> None:
    if engine.dialect.name != "sqlite":
        return

    workspace_scoped_tables = [
        "roles",
        "user_workspaces",
        "collectors",
        "capture_tasks",
        "capture_batches",
        "raw_capture_records",
        "standard_detail_batches",
        "standard_details",
        "field_definitions",
        "field_role_configs",
        "key_field_sets",
        "match_rules",
        "print_template_configs",
        "products",
        "product_skus",
        "stalls",
        "image_assets",
        "report_batches",
        "report_lines",
        "exception_records",
        "export_records",
        "operation_logs",
    ]

    with engine.begin() as connection:
        inspector = inspect(connection)
        table_names = set(inspector.get_table_names())
        if "workspaces" not in table_names:
            return

        workspace_columns = {column["name"] for column in inspector.get_columns("workspaces")}
        if "tenant_id" not in workspace_columns:
            connection.exec_driver_sql("ALTER TABLE workspaces ADD COLUMN tenant_id INTEGER")
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_workspaces_tenant_id ON workspaces (tenant_id)"
            )

        table_columns = {
            table_name: {column["name"] for column in inspector.get_columns(table_name)}
            for table_name in table_names
        }
        if "collectors" in table_columns:
            if "token_hash" not in table_columns["collectors"]:
                connection.exec_driver_sql("ALTER TABLE collectors ADD COLUMN token_hash VARCHAR(255)")
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_collectors_token_hash ON collectors (token_hash)"
                )
            if "is_enabled" not in table_columns["collectors"]:
                connection.exec_driver_sql(
                    "ALTER TABLE collectors ADD COLUMN is_enabled BOOLEAN DEFAULT 1 NOT NULL"
                )
            if "status_payload" not in table_columns["collectors"]:
                connection.exec_driver_sql("ALTER TABLE collectors ADD COLUMN status_payload JSON")

        if "capture_tasks" in table_columns:
            if "started_at" not in table_columns["capture_tasks"]:
                connection.exec_driver_sql("ALTER TABLE capture_tasks ADD COLUMN started_at VARCHAR(64)")
            if "ended_at" not in table_columns["capture_tasks"]:
                connection.exec_driver_sql("ALTER TABLE capture_tasks ADD COLUMN ended_at VARCHAR(64)")

        if "products" in table_columns and "stall_id" not in table_columns["products"]:
            connection.exec_driver_sql("ALTER TABLE products ADD COLUMN stall_id INTEGER")
            connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_products_stall_id ON products (stall_id)")

        if "product_skus" in table_columns and "stall_id" not in table_columns["product_skus"]:
            connection.exec_driver_sql("ALTER TABLE product_skus ADD COLUMN stall_id INTEGER")
            connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_product_skus_stall_id ON product_skus (stall_id)")

        if "raw_capture_records" in table_columns:
            if "source_component" not in table_columns["raw_capture_records"]:
                connection.exec_driver_sql(
                    "ALTER TABLE raw_capture_records ADD COLUMN source_component VARCHAR(128)"
                )
            if "dedupe_key" not in table_columns["raw_capture_records"]:
                connection.exec_driver_sql("ALTER TABLE raw_capture_records ADD COLUMN dedupe_key VARCHAR(255)")
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_raw_capture_records_dedupe_key ON raw_capture_records (dedupe_key)"
                )
            if "captured_at" not in table_columns["raw_capture_records"]:
                connection.exec_driver_sql("ALTER TABLE raw_capture_records ADD COLUMN captured_at VARCHAR(64)")

        for table_name in workspace_scoped_tables:
            if table_name not in table_names:
                continue

            columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "tenant_id" not in columns:
                connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN tenant_id INTEGER")
                connection.exec_driver_sql(
                    f"CREATE INDEX IF NOT EXISTS ix_{table_name}_tenant_id ON {table_name} (tenant_id)"
                )

            connection.exec_driver_sql(
                f"""
                UPDATE {table_name}
                SET tenant_id = (
                    SELECT workspaces.tenant_id
                    FROM workspaces
                    WHERE workspaces.id = {table_name}.workspace_id
                )
                WHERE tenant_id IS NULL
                  AND workspace_id IS NOT NULL
                """
            )
