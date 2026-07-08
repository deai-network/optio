from optio_agents import pane_surfacing as ps


def test_pane_log_path_outside_workdir_and_keyed_by_task():
    p = ps.pane_log_path("/tasks/abc/workdir", "grok")
    assert p == "/tmp/optio-panes/abc/grok-pane.log"


def test_pane_log_path_root_env_override(monkeypatch):
    monkeypatch.setenv("OPTIO_PANES_DIR", "/var/panes")
    p = ps.pane_log_path("/tasks/abc/workdir", "codex", root_env="OPTIO_PANES_DIR")
    assert p == "/var/panes/abc/codex-pane.log"


def test_pipe_pane_cmd_targets_session_and_appends():
    cmd = ps.pipe_pane_cmd("/usr/bin/tmux", "/sock", "sess", "/tmp/p/grok-pane.log")
    assert "pipe-pane" in cmd and "-t sess" in cmd
    assert "cat >>" in cmd and "grok-pane.log" in cmd


def test_error_tail_snippet_tails_ansi_stripped_into_log():
    snip = ps.error_tail_snippet("/wd/optio.log", "/tmp/p/grok-pane.log", "grok")
    assert "tail -n 150" in snip
    assert "grok-pane.log" in snip
    assert "LC_ALL=C sed" in snip  # ANSI strip
    assert ">> /wd/optio.log" in snip


def test_mkdir_pane_dir_cmd():
    assert ps.mkdir_pane_dir_cmd("/tmp/p/x/grok-pane.log") == "mkdir -p /tmp/p/x"
