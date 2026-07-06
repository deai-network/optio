def test_package_imports():
    import optio_antigravity
    assert hasattr(optio_antigravity, "create_antigravity_task")
    assert hasattr(optio_antigravity, "AntigravityTaskConfig")
