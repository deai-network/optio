# optio-agents-all

Meta-factory over every wrapped optio agent engine. Exposes a single
`create_task(process_id, name, config, description=None, metadata=None)` that
dispatches to the correct per-engine factory based on `config.agent_type`, over
a tagged discriminated union (`AgentTaskConfig`) of the seven engine
`TaskConfig` dataclasses.

```python
import optio_agents_all as aa

cfg = aa.ClaudeCodeTaskConfig(consumer_instructions="…")
task = aa.create_task(process_id, name, cfg)
```

Re-exports the seven `<Engine>TaskConfig` classes, the seven
`create_<engine>_task` factories, the `AgentType` literal, and the
`AgentTaskConfig` union.
