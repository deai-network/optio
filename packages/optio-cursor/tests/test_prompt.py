from optio_cursor.prompt import compose_agents_md


def test_agents_md_has_protocol_and_instructions():
    md = compose_agents_md("BUILD THE THING", host_protocol=True)
    assert "BUILD THE THING" in md
    assert "STATUS:" in md and "DELIVERABLE:" in md and "DONE" in md
    assert "BROWSER:" in md


def test_resume_section_names_cursor_store_and_sandbox_note():
    out = compose_agents_md("x", fs_isolation_dirs=[("/wd", "rwx")])
    assert "**Your `home/.cursor/` directory" in out
    assert "**Filesystem access:**" in out


def test_no_prompt_text_of_its_own():
    import inspect
    import optio_cursor.prompt as m
    src = inspect.getsource(m)
    assert "This harness may pause your session" not in src
    assert "You are running inside a coordination harness" not in src
