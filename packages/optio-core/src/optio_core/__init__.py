"""Optio — reusable async process management library."""

from optio_core.models import (
    TaskInstance, TaskInstanceCore, ChildResult, ChildHandle, LaunchBlocked,
    LaunchOutcome, CancelOutcome, DismissOutcome, MongoStore,
)
from optio_core.exceptions import LaunchError, ResultNotPublished
from optio_core.lifecycle import Optio

_instance = Optio()

init = _instance.init
run = _instance.run
shutdown = _instance.shutdown
adhoc_define = _instance.adhoc_define
adhoc_delete = _instance.adhoc_delete
launch = _instance.launch
launch_and_wait = _instance.launch_and_wait
launch_and_await_result = _instance.launch_and_await_result
get_published_result = _instance.get_published_result
cancel = _instance.cancel
dismiss = _instance.dismiss
resync = _instance.resync
get_process = _instance.get_process
list_processes = _instance.list_processes
block_launches = _instance.block_launches
unblock_launches = _instance.unblock_launches
group_cancel = _instance.group_cancel
group_cancel_and_wait = _instance.group_cancel_and_wait

__all__ = [
    "TaskInstance", "TaskInstanceCore", "ChildResult", "ChildHandle",
    "LaunchBlocked",
    "LaunchOutcome", "CancelOutcome", "DismissOutcome",
    "LaunchError", "ResultNotPublished",
    "init", "run", "shutdown",
    "adhoc_define", "adhoc_delete",
    "launch", "launch_and_wait", "launch_and_await_result",
    "get_published_result", "cancel", "dismiss", "resync",
    "get_process", "list_processes",
    "block_launches", "unblock_launches",
    "group_cancel", "group_cancel_and_wait",
    "rpc_server", "mongo_store", "MongoStore",
]


def __getattr__(name: str):
    """Module-level attribute lookup for runtime-populated attributes.

    `rpc_server` is set on the singleton _instance during init(); a normal
    `rpc_server = _instance.rpc_server` binding at module import time would
    capture None forever. PEP 562 __getattr__ forwards reads on access.
    """
    if name == "rpc_server":
        return _instance.rpc_server
    if name == "mongo_store":
        # Runtime-populated: the (db, prefix) binding only exists after init().
        # NB: not "store" — that name is the optio_core.store submodule.
        return _instance.mongo_store
    raise AttributeError(f"module 'optio_core' has no attribute {name!r}")
