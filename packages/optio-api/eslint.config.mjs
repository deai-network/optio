import tseslint from 'typescript-eslint';

export default tseslint.config({
  files: ['src/**/*.ts'],
  ignores: ['src/**/__tests__/**'],
  plugins: { '@typescript-eslint': tseslint.plugin },
  languageOptions: { parser: tseslint.parser },
  rules: {
    'no-restricted-syntax': [
      'error',
      {
        selector:
          "CallExpression[callee.property.name=/^(insertOne|insertMany|updateOne|updateMany|deleteOne|deleteMany|replaceOne|findOneAndUpdate|findOneAndReplace|findOneAndDelete|bulkWrite)$/]",
        message:
          "Direct Mongo writes are forbidden in packages/optio-api/src/. Mutations must go through the engine via OptioEngineClient.<method>().",
      },
    ],
  },
});
