import { AGENTS, type AgentType } from './agents.generated.js';

/** Canonical, user-facing metadata for an agent engine. */
export interface AgentInfo {
  slug: AgentType;
  name: string;
  url: string;
}

/** Canonical metadata for the given engine slug. */
export function getAgentInfo(slug: AgentType): AgentInfo {
  return AGENTS[slug];
}
