"""Tests for LocalHost.run_command / put_file_to_host / fetch_bytes_from_host / resolve_host_home."""

import os

import pytest

from optio_host.host import LocalHost


pytestmark = pytest.mark.asyncio


@pytest.fixture
def local_host(tmp_workdir):
    return LocalHost(taskdir=tmp_workdir)


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


async def test_put_file_path_source(local_host, tmp_path):
    await local_host.setup_workdir()
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello world")
    target = os.path.join(local_host.workdir, "data", "out.bin")
    await local_host.put_file_to_host(str(src), target)
    with open(target, "rb") as fh:
        assert fh.read() == b"hello world"


async def test_put_file_bytes_source(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "x.bin")
    await local_host.put_file_to_host(b"raw bytes", target)
    with open(target, "rb") as fh:
        assert fh.read() == b"raw bytes"


async def test_put_file_async_iterator_source(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "y.bin")

    async def chunks():
        yield b"part1-"
        yield b"part2"

    await local_host.put_file_to_host(chunks(), target)
    with open(target, "rb") as fh:
        assert fh.read() == b"part1-part2"


async def test_put_file_creates_parent_dirs(local_host):
    await local_host.setup_workdir()
    deep = os.path.join(local_host.workdir, "a", "b", "c", "out.txt")
    await local_host.put_file_to_host(b"deep", deep)
    with open(deep, "rb") as fh:
        assert fh.read() == b"deep"


async def test_put_file_atomic_no_tmp_left_on_success(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "atomic.bin")
    await local_host.put_file_to_host(b"ok", target)
    siblings = os.listdir(os.path.dirname(target))
    assert all(not s.endswith(".tmp") and ".tmp." not in s for s in siblings)


async def test_put_file_replaces_existing_atomically(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "replace.bin")
    with open(target, "wb") as fh:
        fh.write(b"OLD")
    await local_host.put_file_to_host(b"NEW", target)
    with open(target, "rb") as fh:
        assert fh.read() == b"NEW"


async def test_put_file_skip_if_unchanged_target_missing(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "first.bin")
    await local_host.put_file_to_host(b"data", target, skip_if_unchanged=True)
    with open(target, "rb") as fh:
        assert fh.read() == b"data"


async def test_put_file_skip_if_unchanged_matches(local_host, tmp_path):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "same.bin")
    with open(target, "wb") as fh:
        fh.write(b"identical content")
    target_mtime_before = os.stat(target).st_mtime_ns
    # Second call must be a no-op.
    await local_host.put_file_to_host(
        b"identical content", target, skip_if_unchanged=True,
    )
    target_mtime_after = os.stat(target).st_mtime_ns
    assert target_mtime_before == target_mtime_after  # untouched
    with open(target, "rb") as fh:
        assert fh.read() == b"identical content"


async def test_put_file_skip_if_unchanged_differs(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "differs.bin")
    with open(target, "wb") as fh:
        fh.write(b"OLD")
    await local_host.put_file_to_host(b"NEW", target, skip_if_unchanged=True)
    with open(target, "rb") as fh:
        assert fh.read() == b"NEW"


async def test_put_file_skip_if_unchanged_iterator_requires_expected_sha(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "iter.bin")

    async def chunks():
        yield b"abc"

    with pytest.raises(ValueError, match="expected_sha256"):
        await local_host.put_file_to_host(
            chunks(), target, skip_if_unchanged=True,
        )


async def test_put_file_skip_if_unchanged_iterator_with_expected_sha_matches(local_host):
    await local_host.setup_workdir()
    import hashlib
    payload = b"streamed payload"
    sha = hashlib.sha256(payload).hexdigest()
    target = os.path.join(local_host.workdir, "iter2.bin")
    with open(target, "wb") as fh:
        fh.write(payload)
    target_mtime_before = os.stat(target).st_mtime_ns

    async def chunks():
        yield payload

    await local_host.put_file_to_host(
        chunks(), target,
        skip_if_unchanged=True, expected_sha256=sha,
    )
    target_mtime_after = os.stat(target).st_mtime_ns
    assert target_mtime_before == target_mtime_after  # skipped


async def test_fetch_bytes_from_host_reads_full(local_host):
    await local_host.setup_workdir()
    target = os.path.join(local_host.workdir, "rd.bin")
    with open(target, "wb") as fh:
        fh.write(b"contents")
    out = await local_host.fetch_bytes_from_host(target)
    assert out == b"contents"


async def test_fetch_bytes_from_host_missing_raises_filenotfound(local_host):
    await local_host.setup_workdir()
    with pytest.raises(FileNotFoundError):
        await local_host.fetch_bytes_from_host(
            os.path.join(local_host.workdir, "no_such")
        )


async def test_resolve_host_home_returns_user_home(local_host):
    expected = os.path.expanduser("~")
    assert await local_host.resolve_host_home() == expected
