"""One-shot schema reconciliation for SQLite.

Stand-in for proper migrations until Alembic is wired. After
`Base.metadata.create_all` (which only creates *missing tables*), this walks
every model table and ALTER TABLE ADD COLUMNs anything the live DB is missing.

Scope is intentionally narrow: nullable columns only, no type changes, no
drops, no index/constraint reconciliation. That covers additive schema bumps
like fill_price, filled_size, clob_order_id without risking data."""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from src.database.base import Base
from src.utils.logging import get_logger

log = get_logger("DATABASE")


def ensure_columns(engine: Engine) -> None:
    """Add any model-declared columns missing from the live DB.

    Refuses non-nullable adds without a server_default — SQLite would error,
    and silently widening to NULL could corrupt invariants the model assumes."""
    if not engine.url.get_backend_name().startswith("sqlite"):
        # PostgreSQL etc. should use Alembic; this helper is SQLite-only.
        return

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all just made it; columns are already correct
            live_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name in live_cols:
                    continue
                if not col.nullable and col.server_default is None and col.default is None:
                    log.warning(
                        "skipping ADD COLUMN %s.%s — non-nullable without default",
                        table.name, col.name,
                    )
                    continue
                col_type = col.type.compile(dialect=engine.dialect)
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}'
                log.info("schema bootstrap: %s", ddl)
                conn.execute(text(ddl))

                # Backfill NULLs to the column's Python default for non-nullable
                # cols. SQLite ALTER TABLE ADD COLUMN doesn't apply Python-side
                # `default=` to existing rows; without this, the next read would
                # surface None where the model promises a float/int/etc.
                if not col.nullable and col.default is not None and not callable(col.default.arg):
                    default_val = col.default.arg
                    log.info(
                        "schema bootstrap: backfill %s.%s = %r",
                        table.name, col.name, default_val,
                    )
                    conn.execute(
                        text(f'UPDATE "{table.name}" SET "{col.name}" = :v WHERE "{col.name}" IS NULL'),
                        {"v": default_val},
                    )
