"""Feldwebel — reusable async process management library."""

from feldwebel.models import TaskInstance, ChildResult
from feldwebel.lifecycle import Feldwebel

_instance = Feldwebel()

init = _instance.init
run = _instance.run
shutdown = _instance.shutdown

__all__ = ["TaskInstance", "ChildResult", "init", "run", "shutdown"]
