"""SQLite connection and bootstrap helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from selfboss.config import load_settings
from selfboss.core.models import AppSettings
from selfboss.data.schema import bootstrap_schema


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection with application defaults."""
    connection = sqlite3.connect(Path(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(db_path: Path | str) -> sqlite3.Connection:
    """Create the database file and schema on first run."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = connect(path)
    bootstrap_schema(connection)
    return connection


def initialize_from_settings(settings: AppSettings | None = None) -> sqlite3.Connection:
    """Initialize the configured local database."""
    resolved = settings or load_settings(create_dirs=True)
    return initialize_database(resolved.db_path)
