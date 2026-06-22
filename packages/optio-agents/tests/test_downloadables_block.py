from optio_agents.prompt import downloadables_block


def test_comparative_mentions_deliverables_and_sentinel():
    s = downloadables_block(comparative=True)
    assert "DELIVERABLE" in s
    assert "optio-file:relpath" in s


def test_standalone_omits_deliverable_comparison():
    s = downloadables_block(comparative=False)
    assert "DELIVERABLE" not in s
    assert "optio-file:relpath" in s
