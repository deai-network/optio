"""Feldwebel process schema migrations."""

from quartiermeister import MigrationRegistry

fw_migrations = MigrationRegistry()

# Import migration modules so they register themselves
import feldwebel.migrations.m001_status_subdocument  # noqa: F401
import feldwebel.migrations.m002_backfill_child_metadata  # noqa: F401
