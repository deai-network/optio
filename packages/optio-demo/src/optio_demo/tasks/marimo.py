"""Marimo reference task for optio widget extensions.

Launches `marimo edit` as a subprocess on a free local port, registers the
upstream with the widget proxy, and sets widgetData to signal the iframe
widget to mount. Exits when cancelled.
"""
import asyncio
import shutil
import socket
import tempfile
from pathlib import Path

from optio_core.context import ProcessContext
from optio_core.models import TaskInstance


NOTEBOOK = Path(__file__).parent.parent / "notebooks" / "sample.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_for_listening(port: int, timeout_s: float = 10.0) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return True
        except (ConnectionRefusedError, OSError):
            await asyncio.sleep(0.1)
    return False


async def run_marimo(ctx: ProcessContext) -> None:
    port = _free_port()
    ctx.report_progress(None, f"Starting marimo on 127.0.0.1:{port}")

    # `marimo edit` autosaves the notebook and writes a `__marimo__/` cache
    # next to it. The bundled notebook lives under `src/`, which `make run`
    # watches — so editing it in place triggers a dev auto-restart that
    # cancels THIS task. Run against a copy in a scratch dir instead, keeping
    # all of marimo's writes out of the watched source tree.
    workdir = tempfile.mkdtemp(prefix="optio-marimo-")
    notebook = Path(workdir) / NOTEBOOK.name
    shutil.copy(NOTEBOOK, notebook)

    proc = await asyncio.create_subprocess_exec(
        "marimo", "edit", str(notebook),
        "--host", "127.0.0.1",
        "--port", str(port),
        "--headless",
        "--no-token",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    try:
        ready = await _wait_for_listening(port)
        if not ready:
            raise RuntimeError(f"marimo did not listen on port {port} within timeout")

        await ctx.set_widget_upstream(f"http://127.0.0.1:{port}")
        # Empty widgetData signals "go live" — the iframe widget will mount.
        await ctx.set_widget_data({})
        ctx.report_progress(None, "marimo is live")

        while ctx.should_continue():
            if proc.returncode is not None:
                if proc.returncode == 0:
                    ctx.report_progress(100, "marimo exited cleanly")
                    return
                raise RuntimeError(f"marimo exited with code {proc.returncode}")
            await asyncio.sleep(0.5)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        shutil.rmtree(workdir, ignore_errors=True)


def get_tasks() -> list[TaskInstance]:
    return [
        TaskInstance(
            execute=run_marimo,
            process_id="marimo-notebook",
            name="Marimo Notebook",
            description="Live marimo notebook embedded via the widget proxy",
            ui_widget="iframe",
        )
    ]
