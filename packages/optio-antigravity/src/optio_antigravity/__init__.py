"""optio-antigravity — run Google Antigravity (agy) as an optio task."""

import logging as _logging

from optio_antigravity.seed_manifest import (
    ANTIGRAVITY_CRED_MANIFEST,
    ANTIGRAVITY_SEED_MANIFEST,
    ANTIGRAVITY_SEED_SUFFIX,
    delete_seed,
    list_seeds,
    purge_seed,
)
from optio_antigravity.session import (
    create_antigravity_task,
    run_antigravity_session,
)
from optio_antigravity.types import AntigravityTaskConfig
from optio_antigravity.verify import verify_and_refresh_seed


# asyncssh emits per-connection INFO lines that flood worker stdout
# once an SSH-backed session starts. Quiet by default.
_logging.getLogger("asyncssh").setLevel(_logging.WARNING)


__all__ = [
    "create_antigravity_task",
    "run_antigravity_session",
    "AntigravityTaskConfig",
    "ANTIGRAVITY_SEED_MANIFEST",
    "ANTIGRAVITY_CRED_MANIFEST",
    "ANTIGRAVITY_SEED_SUFFIX",
    "delete_seed",
    "list_seeds",
    "purge_seed",
    "verify_and_refresh_seed",
]
