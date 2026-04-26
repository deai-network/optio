"""Tests for LocalHost.run_command / put_file_to_host / fetch_bytes_from_host / resolve_host_home."""

import os
import sys

import pytest

from optio_opencode.host import LocalHost


pytestmark = pytest.mark.asyncio


@pytest.fixture
def local_host(tmp_workdir):
    return LocalHost(taskdir=tmp_workdir, opencode_cmd=[sys.executable, "-c", "pass"])


async def test_run_command_captures_stdout(local_host):
    await local_host.setup_workdir()
    result = await local_host.run_command("echo hello")
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello"
    assert result.stderr == ""


async def test_run_command_captures_stderr_and_exit_code(local_host):
    await local_host.setup_workdir()
    result = await local_host.run_command("echo oops 1>&2; exit 7")
    assert result.exit_code == 7
    assert result.stdout == ""
    assert "oops" in result.stderr


async def test_run_command_default_cwd_is_workdir(local_host):
    await local_host.setup_workdir()
    result = await local_host.run_command("pwd")
    assert result.stdout.strip() == os.path.realpath(local_host.workdir)


async def test_run_command_cwd_override(local_host, tmp_path):
    await local_host.setup_workdir()
    result = await local_host.run_command("pwd", cwd=str(tmp_path))
    assert result.stdout.strip() == os.path.realpath(str(tmp_path))


async def test_run_command_env_override(local_host):
    await local_host.setup_workdir()
    result = await local_host.run_command(
        'echo "$MY_VAR"', env={"MY_VAR": "marker", "PATH": os.environ["PATH"]},
    )
    assert result.stdout.strip() == "marker"
