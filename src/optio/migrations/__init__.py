"""Optio process schema migrations."""

from quaestor import MigrationRegistry

fw_migrations = MigrationRegistry()

# Import migration modules so they register themselves
import optio.migrations.m001_status_subdocument  # noqa: F401
import optio.migrations.m002_backfill_child_metadata  # noqa: F401
