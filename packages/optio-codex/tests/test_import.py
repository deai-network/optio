def test_public_surface():
    import optio_codex

    assert hasattr(optio_codex, "create_codex_task")
    assert hasattr(optio_codex, "run_codex_session")
    assert hasattr(optio_codex, "CodexTaskConfig")