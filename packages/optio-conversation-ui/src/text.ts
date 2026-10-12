import { useContext } from 'react';
import { ConfigProvider } from 'antd';

/** A task-defined text (optio_agents.text.Text): a plain string, or a map of
 *  language code (e.g. "en", "hu", "de") to text. */
export type LocalizedText = string | Record<string, string>;

/** `text` in `language`: a plain string as it is; from a language map, the
 *  language's entry, else the `en` entry, else the first entry (as
 *  optio_agents.text.resolve_text). */
export function resolveText(text: LocalizedText, language: string): string {
  if (typeof text === 'string') return text;
  return text[language] ?? text.en ?? Object.values(text)[0] ?? '';
}

/** The UI language: the language of antd's ConfigProvider locale (a host sets
 *  e.g. en_US / hu_HU / de_DE), `en` without one. */
export function useLanguage(): string {
  return useContext(ConfigProvider.ConfigContext).locale?.locale ?? 'en';
}
