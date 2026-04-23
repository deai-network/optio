from optio_opencode.prompt import BASE_PROMPT, compose_agents_md


def test_base_prompt_contains_all_keywords():
    for kw in ("STATUS:", "DELIVERABLE:", "DONE", "ERROR"):
        assert kw in BASE_PROMPT


def test_base_prompt_mentions_log_and_deliverables_paths():
    assert "./optio.log" in BASE_PROMPT
    assert "./deliverables/" in BASE_PROMPT


def test_base_prompt_contains_task_framing():
    assert "## Task" in BASE_PROMPT
    # The framing paragraph.
    assert "ask questions and dialogue with the human" in BASE_PROMPT


def test_compose_agents_md_appends_consumer_instructions_verbatim():
    out = compose_agents_md("please compute 2 + 2")
    assert out.startswith(BASE_PROMPT)
    # Exactly one blank line separates the base prompt from consumer's text.
    assert out.endswith("please compute 2 + 2\n")
    assert "\n\nplease compute 2 + 2\n" in out


def test_compose_agents_md_empty_consumer_still_ends_cleanly():
    out = compose_agents_md("")
    assert out.startswith(BASE_PROMPT)
    assert out.endswith("\n")
