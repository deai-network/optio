"""tmux socket path must stay well under the Linux ``sun_path`` 108-byte limit.

Regression for: a workdir-derived socket (``${workdir}/tmux.sock``) overflowed
``sun_path`` for deep $HOME + long processId, so tmux failed with "File name
too long", claude never launched, and the driver bailed with the misleading
"body returned before DONE was observed".
"""
from optio_claudecode import host_actions

# The exact failing case from the field report (118-char workdir; +"/tmux.sock"
# = 120 bytes, 12 over the 108 limit).
_FAILING_WORKDIR = (
    "/home/excavator/.local/share/optio-claudecode/"
    "komuna-live__analyze_6a0dbf92f4bcc83c10e0fef2_claudecode/workdir"
)


class _H:
    def __init__(self, workdir: str) -> None:
        self.workdir = workdir


def test_socket_path_under_sun_path_limit_for_failing_case():
    sock = host_actions._tmux_socket_path(_H(_FAILING_WORKDIR))
    # 104 is the cross-platform-safe ceiling (macOS/BSD ~104, Linux 108).
    assert len(sock.encode("utf-8")) < 104, sock


def test_socket_path_bounded_for_pathologically_long_workdir():
    sock = host_actions._tmux_socket_path(_H("/x" * 4000 + "/workdir"))
    assert len(sock.encode("utf-8")) < 104, sock


def test_socket_path_deterministic_per_workdir():
    h = _H(_FAILING_WORKDIR)
    assert host_actions._tmux_socket_path(h) == host_actions._tmux_socket_path(h)


def test_socket_path_differs_for_different_workdirs():
    a = host_actions._tmux_socket_path(_H("/home/a/workdir"))
    b = host_actions._tmux_socket_path(_H("/home/b/workdir"))
    assert a != b


def test_socket_path_lives_in_tmp_and_is_a_socket():
    sock = host_actions._tmux_socket_path(_H(_FAILING_WORKDIR))
    assert sock.startswith("/tmp/")
    assert sock.endswith(".sock")
