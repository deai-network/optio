def test_public_surface():
    import optio_grok
    assert hasattr(optio_grok, "create_grok_task")
    assert hasattr(optio_grok, "GrokTaskConfig")
