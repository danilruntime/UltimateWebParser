"""Shared helpers for mapping SQLite rows to domain models."""
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from uwp.timeutil import from_db, to_db


def dt(value: datetime | None) -> str | None:
    """Serialize an optional datetime for storage."""
    return None if value is None else to_db(value)


def parse_dt(value: str | None) -> datetime | None:
    """Parse an optional stored datetime."""
    return None if value is None else from_db(value)


def bool_to_db(value: bool) -> int:
    return 1 if value else 0


def build_update(fields: Mapping[str, Any], columns: Mapping[str, Any]) -> tuple[str, list[Any]]:
    """Build ``SET`` clause and params for ``update(id, **fields)``.

    ``columns`` maps public field names to ``(column, converter)`` pairs; the
    converter turns the Python value into its stored representation.
    """
    if not fields:
        raise ValueError("no fields to update")
    assignments: list[str] = []
    params: list[Any] = []
    for name, value in fields.items():
        if name not in columns:
            raise ValueError(f"unknown field: {name}")
        column, convert = columns[name]
        assignments.append(f"{column} = ?")
        params.append(convert(value))
    return ", ".join(assignments), params
