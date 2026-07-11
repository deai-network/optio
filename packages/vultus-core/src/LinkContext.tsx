import { createContext, useContext, type ComponentType, type ReactNode } from 'react';

/**
 * How an internal (`/`-prefixed) markdown link renders. Hosts with a router
 * inject an SPA link; the default is a full-navigation anchor so router-less
 * consumers (optio-conversation-ui) work with zero setup.
 */
export type InternalLinkComponent = ComponentType<{ href: string; children?: ReactNode }>;

const DefaultInternalLink: InternalLinkComponent = ({ href, children }) => (
  <a href={href}>{children}</a>
);

export const InternalLinkContext = createContext<InternalLinkComponent>(DefaultInternalLink);

export function useInternalLink(): InternalLinkComponent {
  return useContext(InternalLinkContext);
}
