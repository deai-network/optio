def test_public_surface():
    import optio_cursor
    assert hasattr(optio_cursor, "create_cursor_task")
    assert hasattr(optio_cursor, "CursorTaskConfig")
