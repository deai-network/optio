"""Feldwebel — reusable async process management library."""

from feldwebel.models import TaskInstance, ChildResult
from feldwebel.lifecycle import Feldwebel

_instance = Feldwebel()

init = _instance.init
run = _instance.run
shutdown = _instance.shutdown
on_command = _instance.on_command
adhoc_define = _instance.adhoc_define
adhoc_delete = _instance.adhoc_delete
launch = _instance.launch
launch_and_wait = _instance.launch_and_wait
cancel = _instance.cancel
dismiss = _instance.dismiss
resync = _instance.resync
get_process = _instance.get_process
list_processes = _instance.list_processes

__all__ = [
    "TaskInstance", "ChildResult",
    "init", "run", "shutdown", "on_command",
    "adhoc_define", "adhoc_delete",
    "launch", "launch_and_wait", "cancel", "dismiss", "resync",
    "get_process", "list_processes",
]
