/// <reference path="./prism-log.d.ts" />
import { PrismLight } from 'react-syntax-highlighter';
import bash from 'react-syntax-highlighter/dist/esm/languages/prism/bash';
import c from 'react-syntax-highlighter/dist/esm/languages/prism/c';
import cpp from 'react-syntax-highlighter/dist/esm/languages/prism/cpp';
import css from 'react-syntax-highlighter/dist/esm/languages/prism/css';
import diff from 'react-syntax-highlighter/dist/esm/languages/prism/diff';
import docker from 'react-syntax-highlighter/dist/esm/languages/prism/docker';
import go from 'react-syntax-highlighter/dist/esm/languages/prism/go';
import ini from 'react-syntax-highlighter/dist/esm/languages/prism/ini';
import java from 'react-syntax-highlighter/dist/esm/languages/prism/java';
import javascript from 'react-syntax-highlighter/dist/esm/languages/prism/javascript';
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json';
import jsx from 'react-syntax-highlighter/dist/esm/languages/prism/jsx';
import log from 'react-syntax-highlighter/dist/esm/languages/prism/log';
import makefile from 'react-syntax-highlighter/dist/esm/languages/prism/makefile';
import markdown from 'react-syntax-highlighter/dist/esm/languages/prism/markdown';
import markup from 'react-syntax-highlighter/dist/esm/languages/prism/markup';
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python';
import r from 'react-syntax-highlighter/dist/esm/languages/prism/r';
import ruby from 'react-syntax-highlighter/dist/esm/languages/prism/ruby';
import rust from 'react-syntax-highlighter/dist/esm/languages/prism/rust';
import scss from 'react-syntax-highlighter/dist/esm/languages/prism/scss';
import shellSession from 'react-syntax-highlighter/dist/esm/languages/prism/shell-session';
import sql from 'react-syntax-highlighter/dist/esm/languages/prism/sql';
import toml from 'react-syntax-highlighter/dist/esm/languages/prism/toml';
import tsx from 'react-syntax-highlighter/dist/esm/languages/prism/tsx';
import typescript from 'react-syntax-highlighter/dist/esm/languages/prism/typescript';
import yaml from 'react-syntax-highlighter/dist/esm/languages/prism/yaml';

// The Prism languages a code block highlights, registered up front (owner-
// approved 2026-10-16). X's CodeHighlighter loaded each language on first use
// through a dynamic import a Vite bundle cannot resolve, so no block was ever
// highlighted in the app, and it suspended while loading. The common languages
// of agent output are bundled instead; any other renders as plain text.
const LANGUAGES: Record<string, unknown> = {
  bash, c, cpp, css, diff, docker, go, ini, java, javascript, json, jsx, log, makefile, markdown, markup,
  python, r, ruby, rust, scss, 'shell-session': shellSession, sql, toml, tsx, typescript, yaml,
};

for (const [name, grammar] of Object.entries(LANGUAGES)) PrismLight.registerLanguage(name, grammar);

// Other names a fence or a file extension uses for them.
const ALIASES: Record<string, string> = {
  ts: 'typescript', mts: 'typescript', cts: 'typescript',
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', node: 'javascript',
  py: 'python', python3: 'python', py3: 'python',
  sh: 'bash', shell: 'bash', zsh: 'bash', ksh: 'bash',
  console: 'shell-session', terminal: 'shell-session', shellsession: 'shell-session', 'sh-session': 'shell-session',
  jsonc: 'json', json5: 'json',
  yml: 'yaml',
  cfg: 'ini', conf: 'ini',
  postgres: 'sql', postgresql: 'sql', psql: 'sql', mysql: 'sql', sqlite: 'sql', plpgsql: 'sql',
  patch: 'diff',
  md: 'markdown',
  html: 'markup', htm: 'markup', xhtml: 'markup', xml: 'markup', svg: 'markup',
  golang: 'go',
  rs: 'rust',
  h: 'c',
  'c++': 'cpp', cc: 'cpp', cxx: 'cpp', hpp: 'cpp', hh: 'cpp',
  rb: 'ruby',
  dockerfile: 'docker',
  make: 'makefile', mk: 'makefile',
};

/** The registered Prism language a code fence's language names, or
 *  undefined when there is none (the block is then plain text). */
export function prismLanguage(lang: string): string | undefined {
  const key = lang.trim().toLowerCase();
  const name = ALIASES[key] ?? key;
  return Object.hasOwn(LANGUAGES, name) ? name : undefined;
}
