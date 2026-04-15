"""Commonplace database package — connection, migration, and schema."""

from commonplace_db.db import DB_PATH, connect, migrate

__all__ = ["DB_PATH", "connect", "migrate"]
