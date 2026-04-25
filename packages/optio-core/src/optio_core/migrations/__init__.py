"""Optio process schema migrations."""

from quaestor import MigrationRegistry

fw_migrations = MigrationRegistry()

# Import migration modules so they register themselves
import optio_core.migrations.m001_status_subdocument  # noqa: F401
import optio_core.migrations.m002_backfill_child_metadata  # noqa: F401
import optio_core.migrations.m003_backfill_has_saved_state  # noqa: F401
