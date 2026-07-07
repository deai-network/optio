"""Reusable, isolation-safe SSH-over-Docker test harness.

Every wrapper package (optio-opencode, optio-codex, optio-cursor, …) exercises
:class:`~optio_host.host.RemoteHost` against a real ``sshd`` container spun up
from a per-package ``docker-compose.sshd.yml``. Historically each suite hand-
assigned a fixed host port (22222, 22223, …) so the suites *could* be run side
by side — but that scheme collides the moment two modules in one package, two
xdist workers, or two packages run concurrently (fixed port reuse + a shared
default compose project name derived from the ``tests/`` directory).

This helper removes the manual bookkeeping: it gives every container a project
name unique per (package, xdist worker) and lets Docker assign an ephemeral
host port, read back via ``docker compose port``. With that, the harness is
safe under ``pytest -n auto`` *and* under several package test processes
running at once.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import AsyncIterator

# Internal container port every docker-compose.sshd.yml exposes for sshd.
_CONTAINER_SSH_PORT = "2222"


def have_docker() -> bool:
    """True when the ``docker`` CLI is on PATH (harness precondition)."""
    return shutil.which("docker") is not None


def _worker_id() -> str:
    """xdist worker id (``gw0``…) or ``main`` when running without xdist."""
    return os.environ.get("PYTEST_XDIST_WORKER", "main")


def _ensure_keypair(keys_dir: Path) -> Path:
    """Ensure ``keys_dir/id_ed25519{,.pub}`` exists; race-safe across workers.

    Multiple xdist workers of the same package share ``keys_dir``. A plain
    ``if not exists: keygen`` races (two workers, or a half-written ``.pub``
    read by a container mid-generation). Guard generation with an atomic
    ``mkdir`` mutex and wait for the public half to materialise.
    """
    keys_dir.mkdir(exist_ok=True)
    priv = keys_dir / "id_ed25519"
    pub = keys_dir / "id_ed25519.pub"
    if pub.exists():
        return priv

    lock = keys_dir / ".keygen.lock"
    deadline = time.time() + 30
    while not pub.exists():
        try:
            lock.mkdir()
        except FileExistsError:
            # Another worker is generating; wait for it to finish.
            if time.time() > deadline:
                raise RuntimeError("timed out waiting for ssh keypair generation")
            time.sleep(0.1)
            continue
        try:
            if not pub.exists():
                subprocess.check_call(
                    ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv)]
                )
        finally:
            with contextlib.suppress(FileNotFoundError):
                lock.rmdir()
    return priv


@contextlib.asynccontextmanager
async def sshd_container(
    compose_path: os.PathLike[str] | str,
    slug: str,
    *,
    user: str = "optiotest",
    ready_timeout: float = 45.0,
    settle: float = 2.0,
) -> AsyncIterator[dict]:
    """Start the sshd container from ``compose_path``, yield connection info.

    ``slug`` names the package (e.g. ``"optio-opencode"``); combined with the
    xdist worker id it forms a unique compose *project* name so concurrent
    containers never share state. The host port is ephemeral and read back
    from Docker, so no two containers can collide on a port either.

    Yields ``{"host", "port", "user", "key_path"}``; tears the container down
    on exit. Skips the test if the container never accepts connections.
    """
    compose_path = Path(compose_path)
    keys_dir = compose_path.parent / "ssh-keys"
    priv = _ensure_keypair(keys_dir)

    project = f"{slug}-sshd-{_worker_id()}"
    base = ["docker", "compose", "-p", project, "-f", str(compose_path)]

    subprocess.check_call(base + ["up", "-d", "--build"])
    try:
        # Ask Docker which ephemeral host port it bound for the sshd container.
        mapping = subprocess.check_output(
            base + ["port", "sshd", _CONTAINER_SSH_PORT]
        ).decode().strip()
        # e.g. "127.0.0.1:49153" (may list several lines; take the first).
        port = int(mapping.splitlines()[0].rsplit(":", 1)[1])

        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.5)
        else:
            import pytest

            pytest.skip("sshd container did not come up")

        # sshd needs a moment past TCP-accept before it authenticates.
        await asyncio.sleep(settle)

        yield {
            "host": "127.0.0.1",
            "port": port,
            "user": user,
            "key_path": str(priv),
        }
    finally:
        subprocess.call(base + ["down"])
