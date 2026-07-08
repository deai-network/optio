# optio-agents-ui

Canonical agent metadata (slug, name, URL) for optio agent engines, for TypeScript
consumers. The data in `src/agents.generated.ts` is **generated** from the Python
source of truth (`optio_agents_all.AGENTS`) via `make codegen` — do not edit it by
hand.

```ts
import { AGENTS, getAgentInfo } from 'optio-agents-ui';

getAgentInfo('claudecode').name; // "Claude Code"
getAgentInfo('grok').url;        // "https://x.ai/cli"
```
