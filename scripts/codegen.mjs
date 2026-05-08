// Wrapper that calls @clamator/codegen's runCli programmatically.
// Needed because the clamator-codegen bin uses an import.meta.url === argv[1]
// isMain check that fails under pnpm's hard-link virtual store layout.
import { runCli } from '@clamator/codegen';
import { parseArgs } from 'node:util';

const { values } = parseArgs({
  options: {
    src:                 { type: 'string' },
    'out-ts':            { type: 'string' },
    'out-py':            { type: 'string' },
    manifest:            { type: 'string' },
    'json-schema-target':{ type: 'string' },
    'ts-contract-import':{ type: 'string' },
    watch:               { type: 'boolean', default: false },
  },
  strict: false,
});

await runCli({
  src:              values.src,
  outTs:            values['out-ts'],
  outPy:            values['out-py'],
  manifest:         values.manifest,
  jsonSchemaTarget: values['json-schema-target'],
  tsContractImport: values['ts-contract-import'],
  watch:            values.watch,
});
