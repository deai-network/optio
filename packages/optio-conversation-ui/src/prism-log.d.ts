// react-syntax-highlighter ships Prism's `log` grammar, but its @types (15.5)
// do not declare it.
declare module 'react-syntax-highlighter/dist/esm/languages/prism/log' {
  const language: unknown;
  export default language;
}
